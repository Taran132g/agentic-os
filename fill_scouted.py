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


def main() -> int:
    if not CACHE.exists():
        print("no scout_jobs.json — run job_scout.py first")
        return 1
    jobs = json.loads(CACHE.read_text())
    # optional top-N limit from a positional arg
    for a in sys.argv[1:]:
        if a.isdigit():
            jobs = jobs[:int(a)]
    jobs = [j for j in jobs if str(j.get("url", "")).startswith("http")]
    if not jobs:
        print("no fillable jobs in cache")
        return 1

    if os.environ.get("FILL_SCOUTED_DRY") == "1":
        for i, j in enumerate(jobs, 1):
            print(f"[{i}] {j['company']} — {j['role']}\n    {j['url']}")
        return 0

    _tg(f"💼 <b>Sending briefs for {len(jobs)} scouted jobs — one at a time.</b>\n"
        f"Each opens in its own window with the brief sent to Gemini. You click "
        f"\"Start task\" on each when ready.")

    from tools.browser_fill import browser_fill
    results, all_ok = [], True
    for i, job in enumerate(jobs, 1):
        job.setdefault("id", f"scouted_{uuid.uuid4().hex[:8]}")
        print(f"\n===== [{i}/{len(jobs)}] {job['company']} — {job['role']} =====")
        try:
            # start_task=False → paste + send the brief, then STOP. Taran clicks
            # "Start task" himself. notify=True → per-job Telegram + screenshot.
            res = browser_fill(job, notify=True, start_task=False)
            ok = bool(res.get("ok"))
            status = res.get("status", "?")
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok, status, res = False, "exception", {"error": str(e)}
        all_ok = all_ok and ok
        results.append({"company": job["company"], "role": job["role"],
                        "ok": ok, "status": status, "error": res.get("error", "")})
        print(f"  -> ok={ok} status={status} {res.get('error','')}")
        # brief settle between windows so panels/spaces don't collide
        if i < len(jobs):
            time.sleep(5)

    done = sum(1 for r in results if r["ok"])
    summary = [f"<b>📩 Scouted briefs sent — {done}/{len(jobs)} ready for you to start</b>", ""]
    for r in results:
        mark = "📩" if r["ok"] else "⚠️"
        tail = "" if r["ok"] else f" — {r['error'][:50]}"
        summary.append(f"{mark} <b>{r['company']}</b>: {r['role'][:40]}{tail}")
    summary.append("\n<i>Each is open in its own Chrome window with the brief sent. "
                   "Review it, click \"Start task\", then submit.</i>")
    _tg("\n".join(summary))

    print(json.dumps(results, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FILL_SCOUTED FAILED: {e}", file=sys.stderr)
        sys.exit(1)
