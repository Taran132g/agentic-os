#!/usr/bin/env python3
"""Headless job-application fire trigger — built for n8n (2026-05-29).

n8n (or any trigger) runs ONE of:

    python3 ~/agentic_os/jobfill_cli.py "<greenhouse_url>"
    python3 ~/agentic_os/jobfill_cli.py "<url>" "SpaceX" "SWE Intern"
    python3 ~/agentic_os/jobfill_cli.py "SpaceX | SWE Intern | <url>"

It runs `browser_fill` in fire-and-verify mode: spawn Chrome window → paste
brief → Start task → 60s verify → Telegram Taran (with screenshot). Taran then
reviews the open tab, fixes small errors, uploads the resume, and submits.

Set JOBFILL_DRY=1 to print the parsed job and exit WITHOUT driving Chrome
(useful for testing the n8n command wiring). Exit 0 = engaged, 1 = needs a look.
"""

import json
import os
import re
import sys
import uuid
from urllib.parse import urlparse

AGENTIC_DIR = os.path.dirname(os.path.abspath(__file__))


def _company_from_url(u: str) -> str:
    """Best-effort company name from common ATS URL shapes."""
    u = u or ""
    for pat in (r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)",
                r"lever\.co/([^/]+)",
                r"ashbyhq\.com/([^/]+)",
                r"myworkdayjobs\.com/[^/]*?/?([^/.]+)"):
        m = re.search(pat, u)
        if m:
            return m.group(1).replace("-", " ").replace("_", " ").title()
    host = urlparse(u).netloc.replace("www.", "")
    return host.split(".")[0].title() if host else "the company"


def _parse(args: list[str]) -> dict:
    raw = " ".join(args).strip()
    url = company = role = None
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        url = next((p for p in parts if p.lower().startswith("http")), None)
        others = [p for p in parts if not p.lower().startswith("http")]
        company = others[0] if len(others) > 0 else None
        role = others[1] if len(others) > 1 else None
    else:
        url = next((a for a in args if a.lower().startswith("http")),
                   args[0] if args else None)
        company = args[1] if len(args) > 1 else None
        role = args[2] if len(args) > 2 else None

    if not url or not url.lower().startswith("http"):
        raise ValueError(f"no http(s) URL found in input: {raw!r}")

    return {
        "id": f"jobfill_{uuid.uuid4().hex[:8]}",
        "company": company or _company_from_url(url),
        "role": role or "the role",
        "url": url,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: jobfill_cli.py <url> [company] [role]", file=sys.stderr)
        return 2

    # Healthcheck: proves webhook → Execute Command → python without Chrome.
    if " ".join(sys.argv[1:]).strip() == "N8N_PING":
        marker = os.path.join(AGENTIC_DIR, "logs", "jobfill_ping.txt")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w") as f:
            f.write("jobfill chain alive\n")
        print(json.dumps({"ping": "ok", "marker": marker}))
        return 0

    job = _parse(sys.argv[1:])

    if os.environ.get("JOBFILL_DRY") == "1":
        print(json.dumps({"dry_run": True, "job": job}, indent=2))
        return 0

    from tools.browser_fill import browser_fill
    res = browser_fill(job)  # notify=True → sends Telegram + screenshot itself
    print(json.dumps({k: v for k, v in res.items() if k != "screenshot_bytes"},
                     indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.path.insert(0, AGENTIC_DIR)
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"JOBFILL FAILED: {e}", file=sys.stderr)
        sys.exit(1)
