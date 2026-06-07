#!/usr/bin/env python3
"""Fill scouted jobs ONE BY ONE (n8n 2026-06-02).

Reads scout_jobs.json (written by job_scout.py) and fills each application
SEQUENTIALLY via browser_fill — spawn a fresh window, open the Gemini panel,
paste the brief, click Start task, confirm it engaged, then move to the next.
Every window is left OPEN so Taran can review + upload résumé + submit.

This replaces the old per-item n8n fan-out (which spawned 5 windows at once and
halted on the first error). Sequential = no window-title ambiguity, and one
failure never blocks the rest.

Run:
    python3 fill_scouted.py            # fill all jobs in scout_jobs.json
    python3 fill_scouted.py 2          # fill only the top N
Env:
    FILL_SCOUTED_DRY=1   list what would be filled, drive nothing
Exit 0 if all engaged, 1 if any need a look.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENTIC_DIR))
load_dotenv(AGENTIC_DIR / ".env")
CACHE = AGENTIC_DIR / "scout_jobs.json"


def _tg(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN"); cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": int(cid), "text": text[:4000],
                            "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=20)
    except Exception:
        pass


QUEUE = AGENTIC_DIR / "job_queue.json"
MAX_ATTEMPTS = 3   # give up on a job after this many failed fill attempts


def _norm(u: str) -> str:
    return str(u or "").split("?")[0].rstrip("/").lower()


def main() -> int:
    from tools.tracker import load_applications, save_application

    queue = json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    # One-time seed: if the queue is empty, fall back to scout_jobs.json so we
    # don't lose the latest scout run when migrating to the queue.
    if not queue and CACHE.exists():
        queue = json.loads(CACHE.read_text())

    applied = {_norm(a.get("url")) for a in load_applications()
               if a.get("status") == "applied" and a.get("url")}
    # Clean the queue: drop already-applied + URL-less entries.
    queue = [j for j in queue
             if str(j.get("url", "")).startswith("http") and _norm(j.get("url")) not in applied]

    if not queue:
        msg = "✅ <b>Apply-jobs</b> — fill queue is empty (every scouted job is applied)."
        print(msg)
        if os.environ.get("FILL_SCOUTED_DRY") != "1":
            _tg(msg)
            QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2))
        return 0

    # FIFO: take the OLDEST jobs first, up to a per-run cap (default 5).
    limit = int(os.environ.get("FILL_LIMIT", "5"))
    for a in sys.argv[1:]:
        if a.isdigit():
            limit = int(a)
    batch, rest = queue[:limit], queue[limit:]

    if os.environ.get("FILL_SCOUTED_DRY") == "1":
        print(f"queue: {len(queue)} waiting; filling oldest {len(batch)} this run:")
        for i, j in enumerate(batch, 1):
            print(f"  [{i}] {j['company']} — {j['role']}  (attempts={j.get('attempts',0)})")
        return 0

    _tg(f"💼 <b>Filling {len(batch)} from the job queue (oldest first).</b> "
        f"{len(queue)} waiting total.\nEach opens in its own window with the brief "
        f"sent to Gemini — click \"Start task\" on each.")

    from tools.browser_fill import browser_fill
    results, retry, dropped = [], [], []
    for i, job in enumerate(batch, 1):
        job.setdefault("id", f"scouted_{uuid.uuid4().hex[:8]}")
        print(f"\n===== [{i}/{len(batch)}] {job['company']} — {job['role']} =====")
        try:
            res = browser_fill(job, notify=True, start_task=False)
            ok = bool(res.get("ok"))
            status = res.get("status", "?")
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok, status, res = False, "exception", {"error": str(e)}

        if ok:
            # completion Telegram fired → record applied; it leaves the queue.
            try:
                save_application(job, status="applied", platform="gemini")
            except Exception as te:
                print("! tracker save failed:", te)
        else:
            job["attempts"] = job.get("attempts", 0) + 1
            (dropped if job["attempts"] >= MAX_ATTEMPTS else retry).append(job)
        results.append({"company": job["company"], "role": job["role"],
                        "ok": ok, "status": status, "error": res.get("error", "")})
        print(f"  -> ok={ok} status={status} {res.get('error','')}")
        if i < len(batch):
            time.sleep(5)

    # Rebuild the queue: failed-but-retryable stay at the FRONT (FIFO), then the
    # untouched tail. Successful + exhausted jobs drop off.
    new_queue = retry + rest
    QUEUE.write_text(json.dumps(new_queue, ensure_ascii=False, indent=2))

    done = sum(1 for r in results if r["ok"])
    summary = [f"<b>📩 Briefs sent — {done}/{len(batch)} ready to start</b>",
               f"<i>queue: {len(new_queue)} still waiting</i>", ""]
    for r in results:
        mark = "📩" if r["ok"] else "⚠️"
        tail = "" if r["ok"] else f" — {r['error'][:45]}"
        summary.append(f"{mark} <b>{r['company']}</b>: {r['role'][:38]}{tail}")
    if dropped:
        summary.append(f"\n🛑 Dropped after {MAX_ATTEMPTS} tries: "
                       + ", ".join(d["company"] for d in dropped))
    summary.append("\n<i>Each is open in Chrome with the brief sent. Review, click "
                   "\"Start task\", submit.</i>")
    _tg("\n".join(summary))

    print(json.dumps(results, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FILL_SCOUTED FAILED: {e}", file=sys.stderr)
        sys.exit(1)
