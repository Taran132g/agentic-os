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
Exit 0 always — per-job outcomes are reported via Telegram + stdout JSON.
(Exiting 1 on a partial success made n8n mark the whole execution as errored,
which made real failures indistinguishable from "4/5 filled, one needs a look".)
"""

import json
import os
import sys
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
    from tools.atomic_state import read_json, write_json, locked_update

    queue = read_json(QUEUE, [])
    if not isinstance(queue, list):
        queue = []
    # One-time seed: if the queue is empty, fall back to scout_jobs.json so we
    # don't lose the latest scout run when migrating to the queue.
    if not queue:
        seed = read_json(CACHE, [])
        if isinstance(seed, list):
            queue = seed

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
            write_json(QUEUE, queue)
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

    # Playwright DOM fill (2026-06-19): replaces the blind OCR/coordinate-click
    # browser_fill.py, which kept missing the form and firing pyautogui clicks
    # into the Dock (opening random apps) instead of filling Gemini. This drives
    # the real page DOM in ONE persistent browser — N tabs, all left open for
    # Taran to review + submit. keep_open=0 here: the batch returns immediately
    # after filling so this runner can post its summary; Taran reviews the tabs
    # while the window stays up (the context closes when the process exits, so
    # the bridge runs this detached — see pais_bridge.run_fill_scouted).
    from tools.pais_browser import browser_fill_pw_batch
    for job in batch:
        job.setdefault("id", f"scouted_{uuid.uuid4().hex[:8]}")
    keep = int(os.environ.get("FILL_KEEP_OPEN", "1800"))
    batch_urls = {_norm(j.get("url")) for j in batch}

    def _finish(fills):
        """Record applied, rebuild the queue, and Telegram the summary. Runs once
        every tab is filled but while the browser is still OPEN, so the summary
        lands immediately and Taran reviews the live tabs."""
        results, retry, dropped = [], [], []
        for i, (job, res) in enumerate(zip(batch, fills), 1):
            print(f"\n===== [{i}/{len(batch)}] {job['company']} — {job['role']} =====")
            ok = bool(res.get("ok"))
            status = res.get("status", "?")
            if ok:
                # Form filled in the browser (Taran reviews + submits). Record
                # applied so it leaves the queue and the vault pipeline advances.
                try:
                    save_application(job, status="applied", platform="playwright")
                except Exception as te:
                    print("! tracker save failed:", te)
                try:                            # advance the vault Job Pipeline row
                    from tools import job_sheet
                    job_sheet.mark_applied(job.get("url", ""))
                except Exception as se:
                    print("! job_sheet mark_applied failed:", se)
            else:
                job["attempts"] = job.get("attempts", 0) + 1
                (dropped if job["attempts"] >= MAX_ATTEMPTS else retry).append(job)
            n_filled = len(res.get("filled", []))
            results.append({"company": job["company"], "role": job["role"],
                            "ok": ok, "status": status, "n_filled": n_filled,
                            "error": res.get("error", "")})
            print(f"  -> ok={ok} status={status} filled={n_filled} {res.get('error','')}")

        # Rebuild the queue under lock so a concurrent scout run's appends survive:
        # reload the current file, drop everything in THIS batch, then put the
        # failed-but-retryable jobs back at the FRONT (FIFO). Successful + exhausted
        # jobs drop off; untouched tail + any new scout additions are preserved.
        def _rebuild(current):
            if not isinstance(current, list):
                current = []
            survivors = [j for j in current if _norm(j.get("url")) not in batch_urls]
            return retry + survivors

        new_queue = locked_update(QUEUE, _rebuild, default=[])

        done = sum(1 for r in results if r["ok"])
        summary = [f"<b>📝 Forms filled — {done}/{len(batch)} ready to submit</b>",
                   f"<i>queue: {len(new_queue)} still waiting</i>", ""]
        for r in results:
            mark = "✅" if r["ok"] else "⚠️"
            tail = (f" — {r['n_filled']} fields" if r["ok"]
                    else f" — {r['error'][:45]}")
            summary.append(f"{mark} <b>{r['company']}</b>: {r['role'][:38]}{tail}")
        if dropped:
            summary.append(f"\n🛑 Dropped after {MAX_ATTEMPTS} tries: "
                           + ", ".join(d["company"] for d in dropped))
        summary.append("\n<i>Each is open in a browser tab with fields pre-filled. "
                       "Review, fix anything custom, and submit yourself.</i>")
        _tg("\n".join(summary))
        print(json.dumps(results, indent=2))

    try:
        browser_fill_pw_batch(batch, keep_open_seconds=keep, after_fill=_finish)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Browser never launched — still do the bookkeeping so the queue advances
        # and Taran hears about it.
        _finish([{"ok": False, "status": "exception", "error": str(e)} for _ in batch])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FILL_SCOUTED FAILED: {e}", file=sys.stderr)
        sys.exit(1)
