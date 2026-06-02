#!/usr/bin/env python3
"""Headless job SEARCH + fill — built for n8n (2026-05-31).

Sibling of jobfill_cli.py. Where jobfill fills ONE known URL, this one SEARCHES
job boards (The Muse + Remotive via tools/jobsearch.py) for N matches and fires
browser_fill on each, in sequence, so Taran ends up with N Chrome tabs prefilled
and ready to review + submit.

n8n (or any trigger) runs:
    python3 ~/agentic_os/jobsearch_cli.py
    python3 ~/agentic_os/jobsearch_cli.py "machine learning intern"
    python3 ~/agentic_os/jobsearch_cli.py "software engineering intern" 5

Args:  [keywords]  [count]   (defaults: "software engineering intern", 5)

Env:
    JOBSEARCH_DRY=1     search only — print the N jobs as JSON, fill nothing
    JOBSEARCH_LIMIT=N   override fill count (default 5; also 2nd positional arg)

Each fill sends its own Telegram (notify=True). A final summary Telegram lists
what was queued. Exit 0 if all engaged, 1 if any needed a look.
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENTIC_DIR))
load_dotenv(AGENTIC_DIR / ".env")


def _send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(chat_id), "text": text[:4000],
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception:
        pass


def main() -> int:
    args = sys.argv[1:]
    keywords = args[0] if args and not args[0].isdigit() else "software engineering intern"
    # count: 2nd positional, or env, default 5
    count = int(os.environ.get("JOBSEARCH_LIMIT", "5"))
    for a in args:
        if a.isdigit():
            count = int(a)
            break

    from tools.jobsearch import search_jobs
    jobs = search_jobs(keywords, count) or []
    # Normalize: ensure each job has an id + http url.
    clean = []
    for j in jobs:
        url = (j.get("url") or "").strip()
        if not url.lower().startswith("http"):
            continue
        j.setdefault("id", f"jobsearch_{uuid.uuid4().hex[:8]}")
        j.setdefault("role", "the role")
        j.setdefault("company", "the company")
        clean.append(j)
    clean = clean[:count]

    if not clean:
        msg = f"🔎 Job search for “{keywords}” returned no fillable URLs."
        print(msg)
        if os.environ.get("JOBSEARCH_DRY") != "1":
            _send_telegram(msg)
        return 1

    # SEARCH-ONLY: emit clean JSON for the n8n "apply-jobs" pipeline to split on,
    # then fill each via jobfill_cli. DRY is the same path (search, fill nothing).
    if os.environ.get("JOBSEARCH_SEARCH_ONLY") == "1" or \
       os.environ.get("JOBSEARCH_DRY") == "1":
        def _clean(s: str) -> str:
            # strip chars that would break the downstream shell-quoted jobfill call
            return re.sub(r'["|`$\\]', "", str(s)).strip()
        out = [{"id": j.get("id"),
                "company": _clean(j.get("company", "")),
                "role": _clean(j.get("role", "")),
                "url": j.get("url", ""),
                "location": _clean(j.get("location", "")),
                "match_score": j.get("match_score")}
               for j in clean]
        print(json.dumps(out, indent=2))
        return 0

    _send_telegram(
        f"🔎 <b>Job search:</b> “{keywords}” → filling {len(clean)} applications. "
        f"Chrome tabs will open one by one; review + submit each yourself."
    )

    from tools.browser_fill import browser_fill
    results, all_ok = [], True
    for i, job in enumerate(clean, 1):
        print(f"\n[{i}/{len(clean)}] {job['company']} — {job['role']}\n  {job['url']}")
        try:
            res = browser_fill(job)  # notify=True → its own Telegram + screenshot
            ok = bool(res.get("ok"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok, res = False, {"ok": False, "error": str(e)}
        all_ok = all_ok and ok
        results.append({"company": job["company"], "role": job["role"],
                        "url": job["url"], "ok": ok})

    summary = ["<b>🔎 Job search fill complete</b>",
               f"<i>“{keywords}” · {len(clean)} jobs</i>", ""]
    for r in results:
        mark = "✅" if r["ok"] else "⚠️"
        summary.append(f"{mark} <b>{r['company']}</b> — {r['role']}")
    _send_telegram("\n".join(summary))

    print(json.dumps(results, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"JOBSEARCH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
