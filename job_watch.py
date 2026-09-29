#!/usr/bin/env python3
"""Banking-tech intern WATCHER — alerts the moment a watched employer opens a
Summer-intern technology posting, so Taran can apply himself (no auto-fill).

Unlike job_scout.py (which blind-scouts fresh AI/SWE roles for the fill queue),
this watches a FIXED shortlist of banking / financial-services / fintech
employers — the ones Taran already reached second rounds / real interviews at
last cycle (Customers Bank, Vanguard, PNC, Reliance Matrix, Block) plus a
broader set of top banks — and pings him only when a NEW tech-intern req goes
live. Built to run daily on the Oracle box via cron.

Flow:  research (claude WebSearch/WebFetch, subscription-billed — no API key)
    -> deterministic live-URL gate (tools.url_verify.filter_open)
    -> dedupe vs state file AND the vault pipeline (never re-alert)
    -> Telegram alert  +  append to the vault Job Pipeline as `🔍 To apply`
    -> persist state.

Modes:
    python3 job_watch.py           # poll -> alert new -> persist (cron daily)
    python3 job_watch.py --dry     # poll + print only; no Telegram, no state, no vault

Env:
    JOB_WATCH_SEASON   target summer year (default: current year + 1)
    JOB_WATCH_DRY=1    same as --dry
Exit 0 always (nothing-new is a healthy run).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from shutil import which

from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")
STATE = AGENTIC_DIR / "job_watch_state.json"   # {norm_url: {company, role, first_seen}}

# ── Watchlist ────────────────────────────────────────────────────────────────
# Proven = employers Taran reached a real interview / second round with last
# cycle. Broad = top banks / financial-services / fintech worth catching early.
# The careers URL is a locator hint for WebSearch; the deterministic gate
# confirms the specific posting is actually live before anything is sent.
WATCHLIST: list[dict] = [
    # ── Proven (interviewed / second round last cycle) ──
    {"company": "Customers Bank",  "careers": "https://www.customersbank.com/careers/",           "proven": True},
    {"company": "Vanguard",        "careers": "https://www.vanguardjobs.com/",                     "proven": True},
    {"company": "PNC",             "careers": "https://careers.pnc.com/",                          "proven": True},
    {"company": "Reliance Matrix", "careers": "https://reliancematrix.com/about-us/careers/",      "proven": True},
    {"company": "Block (Square)",  "careers": "https://block.xyz/careers/jobs",                    "proven": True},
    # ── Broader banking / fintech net ──
    {"company": "Capital One",     "careers": "https://www.capitalonecareers.com/",                "proven": False},
    {"company": "Citizens",        "careers": "https://jobs.citizensbank.com/",                    "proven": False},
    {"company": "Citi",            "careers": "https://jobs.citi.com/",                            "proven": False},
    {"company": "BlackRock",       "careers": "https://careers.blackrock.com/",                    "proven": False},
    {"company": "Goldman Sachs",   "careers": "https://www.goldmansachs.com/careers/students/",    "proven": False},
    {"company": "JPMorgan Chase",  "careers": "https://careers.jpmorgan.com/us/en/students",       "proven": False},
    {"company": "Fidelity",        "careers": "https://jobs.fidelity.com/",                        "proven": False},
    {"company": "American Express", "careers": "https://www.americanexpress.com/en-us/careers/",   "proven": False},
    {"company": "Capital Group",   "careers": "https://www.capitalgroup.com/about-us/careers.html", "proven": False},
]


def _target_year() -> int:
    env = os.environ.get("JOB_WATCH_SEASON")
    if env and env.isdigit():
        return int(env)
    return datetime.now().year + 1          # Aug 2026 -> Summer 2027 cycle


def _tg_text(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid):
        return
    payload = json.dumps({"chat_id": int(cid), "text": text[:4090],
                          "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:                   # notification must never crash the run
        print("! telegram:", e)


def _research(year: int) -> list[dict]:
    """Ask claude to find CURRENTLY-OPEN Summer-{year} tech-intern reqs at the
    watched employers. Returns a JSON array (possibly empty)."""
    if not which("claude"):
        raise RuntimeError("claude CLI not on PATH")

    roster = "\n".join(
        f"- {w['company']}  (careers: {w['careers']})"
        + ("  [PROVEN — Taran already interviewed here]" if w["proven"] else "")
        for w in WATCHLIST)

    prompt = f"""You are a job-opening watcher. Today is {datetime.now():%Y-%m-%d}.

For EACH employer below, use WebSearch + WebFetch to check whether they currently
have an OPEN **Summer {year}** (or the next open intern cycle) **TECHNOLOGY
internship / co-op** with a DIRECT application URL. Technology = Software
Engineer, AI/ML, Application Development, Data Engineering, Technology Analyst,
Quantitative Developer, or similar build-software roles.

EMPLOYERS TO CHECK:
{roster}

RULES:
- Internships / co-ops ONLY — exclude full-time and new-grad roles.
- UNDERGRADUATE-eligible ONLY — Taran is a rising-senior undergraduate who
  graduates May 2028. EXCLUDE roles that require a Master's or PhD (or are
  labeled "Current Master's" / "Graduate" / "PhD").
- TECHNOLOGY roles ONLY — exclude financial-advisor, sales, actuarial, audit,
  marketing, and pure business-analyst postings.
- STRONGLY PREFER US-based roles (Taran needs US work authorization). Include a
  non-US posting ONLY if the employer has no US tech-intern req open, and say so.
- Include an employer ONLY if it has a real, currently-open posting with a
  working application URL. If an employer has nothing open, OMIT it entirely —
  do NOT invent a URL. It is correct and expected to return fewer entries than
  the roster, including an empty array.
- Prefer the official careers / Workday / Greenhouse posting over aggregators.
- If an employer has several qualifying tech-intern reqs, return the best one.

Output ONLY a JSON array (no prose, no code fences):
[{{"company":"","role":"","url":"<direct application URL>","location":"",
"posted":"<approx posting date or ''>",
"why_fit":"<one sentence — why this fits an AI-Engineering / SWE candidate>"}}]"""

    cmd = ["claude", "-p", prompt,
           "--allowedTools", "WebSearch,WebFetch",
           "--dangerously-skip-permissions"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    raw = re.sub(r"```(?:json)?|```", "", (res.stdout or "").strip())
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON array in claude output: {raw[:200]}")
    data = json.loads(m.group(0))
    return data if isinstance(data, list) else []


def _seen_urls() -> set[str]:
    """URLs we must never re-alert: already alerted (state) OR already tracked in
    the vault pipeline (so a role Taran is already on doesn't ping again)."""
    from tools.atomic_state import read_json
    seen = {_norm(u) for u in (read_json(STATE, {}) or {}).keys()}
    try:
        from tools import job_sheet
        seen |= {_norm(u) for u in job_sheet.existing_urls()}
    except Exception as e:
        print("(pipeline dedupe skipped:", str(e)[:120], ")")
    return seen


def _norm(u: str) -> str:
    return str(u or "").split("?")[0].rstrip("/").lower()


def _proven(company: str) -> bool:
    c = (company or "").lower()
    return any(w["proven"] and w["company"].lower().split(" (")[0] in c for w in WATCHLIST)


def main() -> int:
    dry = "--dry" in sys.argv[1:] or os.environ.get("JOB_WATCH_DRY") == "1"
    year = _target_year()
    stamp = datetime.now().strftime("%a %b %d")

    try:
        found = _research(year)
    except Exception as e:
        msg = f"⚠️ <b>Job watcher failed</b>: {e}"
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    found = [j for j in found if str(j.get("url", "")).startswith("http")]

    # Deterministic live-URL gate — drop hallucinated / dead / closed postings.
    try:
        from tools.url_verify import filter_open
        found, dropped = filter_open(found)
        for j in dropped:
            print(f"[watch] dropped dead/closed: {j.get('company','?')} — {j.get('url','')}")
    except Exception as e:
        print("(url verify skipped:", str(e)[:120], ")")

    # Only NEW openings (not previously alerted, not already in the pipeline).
    seen = _seen_urls()
    fresh = [j for j in found if _norm(j.get("url")) not in seen]
    # proven employers first, then by company name
    fresh.sort(key=lambda j: (not _proven(j.get("company", "")), j.get("company", "")))

    if not fresh:
        msg = (f"🏦 <b>Banking-tech watcher — {stamp}</b>\n"
               f"No new Summer {year} tech-intern openings at your watchlist today "
               f"({len(found)} live checked).")
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    # ── Alert + mirror into the vault pipeline ──
    lines = [f"🏦 <b>NEW banking-tech intern openings — {stamp}</b>",
             f"<i>Summer {year} · apply now</i>", ""]
    for j in fresh:
        tag = "⭐ " if _proven(j.get("company", "")) else ""
        loc = j.get("location", "")
        posted = j.get("posted", "")
        meta = " · ".join(x for x in (loc, posted) if x)
        lines.append(f"<b>{tag}{j.get('company','?')}</b> — {j.get('role','?')}")
        if meta:
            lines.append(f"  {meta}")
        if j.get("why_fit"):
            lines.append(f"  <i>{j['why_fit']}</i>")
        lines.append(f"  {j.get('url')}")
        lines.append("")
    lines.append("<i>⭐ = you already interviewed here last cycle. Added to your Job Pipeline as “To apply”.</i>")
    digest = "\n".join(lines)
    print(digest)

    if not dry:
        # tag the pipeline "why" so watchlist opens are distinguishable
        for j in fresh:
            j["why"] = ("🏦 Watchlist open" + (" · ⭐ prior interview" if _proven(j.get("company", "")) else "")
                        + (f" — {j['why_fit']}" if j.get("why_fit") else ""))
        try:
            from tools import job_sheet
            job_sheet.append_jobs(fresh)
        except Exception as e:
            print("(pipeline append skipped:", str(e)[:120], ")")

        from tools.atomic_state import locked_update
        today = datetime.now().strftime("%Y-%m-%d")

        def _mutate(state):
            if not isinstance(state, dict):
                state = {}
            for j in fresh:
                state[_norm(j.get("url"))] = {
                    "company": j.get("company", ""),
                    "role": j.get("role", ""),
                    "first_seen": today,
                }
            return state

        locked_update(STATE, _mutate, default={})
        _tg_text(digest)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"JOB_WATCH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
