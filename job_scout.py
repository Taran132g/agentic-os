#!/usr/bin/env python3
"""Job scout — claude-researched Summer 2027 internship matches (n8n 2026-06-02).

Replaces the shallow Muse/Remotive keyword search for the SCHEDULED apply-jobs
run. Uses the `claude` CLI (WebSearch + WebFetch, subscription-billed — no API
key) to find RECENTLY-POSTED Summer 2027 SWE/AI/ML internships that match
Taran's actual resume, verify each application URL exists, rank by fit, send a
ranked Telegram digest, and cache results to scout_jobs.json for the fill step.

Modes:
    python3 job_scout.py            # research → digest → cache (scheduled 9am)
    python3 job_scout.py --emit     # print cached scout_jobs.json (fill pipeline)

Env:
    JOB_SCOUT_DRY=1   research + print, no Telegram, no cache write
    JOB_SCOUT_N=5     number of matches to find (default 5)
Exit 0 always (an empty scout is still a successful run).
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from shutil import which

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")
CACHE = AGENTIC_DIR / "scout_jobs.json"
VAULT_RESUME = (Path.home() / "Library/Mobile Documents/iCloud~md~obsidian"
                / "Documents/Digital Brain" / "About Taran/Resume.md")

RESUME_FALLBACK = ("Penn State AI Engineering student (grad May 2028), minor in "
                   "Economics. Skills: Python, AWS, Firebase, Flask, Django, "
                   "React, ROS2, ML. EV-charging IoT intern; co-founded Piontrix "
                   "(tech consulting); Penn State DeFi Club Trading & Tech Lead; "
                   "built TNFund trading bot.")


def _tg_text(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN"); cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": int(cid), "text": text[:4090],
                            "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=20)
    except Exception as e:
        print("! telegram:", e)


def _emit() -> int:
    """Print the cached scouted jobs as a JSON array (for the n8n fill pipeline)."""
    print(CACHE.read_text() if CACHE.exists() else "[]")
    return 0


def _research(n: int) -> list[dict]:
    if not which("claude"):
        raise RuntimeError("claude CLI not on PATH")
    resume = VAULT_RESUME.read_text()[:4000] if VAULT_RESUME.exists() else RESUME_FALLBACK
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""You are a job scout for a candidate. Today is {today}.

Use WebSearch to find {n} RECENTLY-POSTED (within roughly the last 30 days)
**Summer 2027** software engineering / AI / ML internships that strongly match
the candidate below. Prefer postings on Greenhouse, Lever, Ashby, Workday, or
official company career pages that have a DIRECT application URL. Use WebFetch to
verify each URL is a real, currently-open application page before including it.
Exclude anything that is not clearly a Summer 2027 term or not a software/AI/ML
role. Rank best-fit first.

CANDIDATE RESUME:
{resume}

Output ONLY a JSON array of up to {n} objects (no prose, no code fences):
[{{"company":"","role":"","url":"<verified application URL>","location":"",
"posted":"<approx posting date>","match_score":<integer 0-100>,
"why_fit":"<one concise sentence on why it fits the candidate>"}}]"""
    cmd = ["claude", "-p", prompt,
           "--allowedTools", "WebSearch,WebFetch",
           "--dangerously-skip-permissions"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    raw = (res.stdout or "").strip()
    raw = re.sub(r"```(?:json)?|```", "", raw)
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON array in claude output: {raw[:200]}")
    data = json.loads(m.group(0))
    return data if isinstance(data, list) else []


def main() -> int:
    if "--emit" in sys.argv[1:]:
        return _emit()

    n = int(os.environ.get("JOB_SCOUT_N", "5"))
    dry = os.environ.get("JOB_SCOUT_DRY") == "1"
    today = datetime.now().strftime("%a %b %d")

    try:
        jobs = _research(n)
    except Exception as e:
        msg = f"⚠️ <b>Job scout failed</b>: {e}"
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    # keep only entries with a real URL, rank by match_score desc
    jobs = [j for j in jobs if str(j.get("url", "")).startswith("http")]
    jobs.sort(key=lambda j: -(j.get("match_score") or 0))
    jobs = jobs[:n]

    if not jobs:
        msg = f"💼 <b>Job scout — {today}</b>\nNo strong Summer 2027 matches found today."
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    if not dry:
        CACHE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))

    lines = [f"💼 <b>Summer 2027 matches — {today}</b>",
             f"<i>{len(jobs)} researched + URL-verified · ranked by fit</i>", ""]
    for j in jobs:
        score = j.get("match_score", "?")
        loc = j.get("location", "")
        posted = j.get("posted", "")
        meta = " · ".join(x for x in (loc, posted) if x)
        lines.append(f"<b>[{score}] {j.get('company','?')}</b> — {j.get('role','?')}")
        if meta:
            lines.append(f"  {meta}")
        if j.get("why_fit"):
            lines.append(f"  <i>{j['why_fit']}</i>")
        lines.append(f"  {j.get('url')}")
        lines.append("")
    lines.append("<i>Reply-ready. POST /webhook/apply to fill these when you're at the desk.</i>")
    digest = "\n".join(lines)

    print(digest)
    if not dry:
        _tg_text(digest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"JOB_SCOUT FAILED: {e}", file=sys.stderr)
        sys.exit(1)
