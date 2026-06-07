#!/usr/bin/env python3
"""piontrix_scout.py — find local businesses that need tech help (2026-06-04).

Lead-gen for Piontrix outreach. Uses the `claude` CLI (WebSearch + WebFetch,
subscription-billed) to find small businesses in the Royersford ↔ King of Prussia
PA corridor that would genuinely benefit from Piontrix's services (web/app dev,
UI/UX, SEO, online ordering/booking, automation) — e.g. no website, outdated or
non-mobile sites, weak online presence. New finds are appended (deduped) to
piontrix_leads.json as uncontacted leads; the piontrix-outreach batch then drafts
personalized outreach for each.

Run:
    python3 piontrix_scout.py          # find + queue new local leads
Env:
    PIONTRIX_SCOUT_DRY=1   print finds, don't write leads
    PIONTRIX_SCOUT_N=8     how many to find per run (default 8)
Exit 0 always.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")
LEADS_FILE = AGENTIC_DIR / "piontrix_leads.json"

# The Royersford → King of Prussia corridor (Montgomery/Chester County, PA)
AREA = ("Royersford, Spring City, Phoenixville, Collegeville, Trappe, Audubon, "
        "Oaks, Eagleville, Norristown, Bridgeport, and King of Prussia PA "
        "(roughly the Route 422 corridor, within ~15 miles of Royersford 19468)")


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


def _domain(website: str) -> str:
    if not website or website.lower() in ("none", "n/a", ""):
        return ""
    w = website if "://" in website else "https://" + website
    return (urlparse(w).netloc or urlparse(w).path).replace("www.", "").strip("/").lower()


def _research(n: int) -> list[dict]:
    if not which("claude"):
        raise RuntimeError("claude CLI not on PATH")
    prompt = f"""You are a lead scout for **Piontrix**, a student-run tech consulting
company (Penn State / Royersford PA). Piontrix helps local businesses MAKE AND SAVE
MONEY with technology: custom websites, online ordering & booking (to cut third-party
commissions), **sales/operations dashboards & analytics**, inventory and customer
data tools, reporting, and workflow automation.

Use WebSearch (and WebFetch to verify) to find {n} REAL independent local businesses
in this area: {AREA}.

TARGET businesses with enough volume/operations to profit from technical work —
e.g. restaurants & cafes (online ordering, sales dashboards), gyms & studios
(membership/retention analytics, booking), salons/spas (booking + revenue
dashboards), retail shops (inventory + sales analytics), auto shops, contractors,
property managers, dental/medical practices, and local e-commerce. The angle is
HELPING THEM MAXIMIZE PROFIT with tech (dashboards, automation, online sales),
not just "you need a website." EXCLUDE big chains and national brands.

CRITICAL: For EACH business, actually FIND a real contact email — check their
website contact/about page, Google Business listing, or Facebook "About" via
WebSearch/WebFetch. Prefer businesses where you can find a real email; only use
"none" if you genuinely cannot find one after searching. A lead with no email is
nearly useless, so prioritize findable contacts.

Output ONLY a JSON array of {n} objects, no prose, no code fences:
[{{"business":"","website":"<url or 'none'>","email":"<real contact email or 'none'>",
"location":"<town, PA>",
"why_fit":"<one sentence: the specific profit-boosting tech opportunity — e.g. a
sales dashboard, online ordering to cut Grubhub fees, booking + retention analytics>"}}]"""
    cmd = ["claude", "-p", prompt,
           "--allowedTools", "WebSearch,WebFetch",
           "--dangerously-skip-permissions"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    raw = re.sub(r"```(?:json)?|```", "", (res.stdout or "").strip())
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError(f"no JSON array from claude: {raw[:200]}")
    data = json.loads(m.group(0))
    return data if isinstance(data, list) else []


def main() -> int:
    n = int(os.environ.get("PIONTRIX_SCOUT_N", "8"))
    dry = os.environ.get("PIONTRIX_SCOUT_DRY") == "1"

    try:
        found = _research(n)
    except Exception as e:
        msg = f"⚠️ <b>Piontrix scout failed</b>: {e}"
        print(msg)
        if not dry:
            _tg(msg)
        return 0

    leads = json.loads(LEADS_FILE.read_text()) if LEADS_FILE.exists() else []
    # dedup keys from existing leads
    seen_biz = {str(l.get("business", "")).strip().lower() for l in leads}
    seen_dom = {_domain(l.get("website", "")) for l in leads if _domain(l.get("website", ""))}

    new = []
    for f in found:
        biz = str(f.get("business", "")).strip()
        web = str(f.get("website", "")).strip()
        if not biz:
            continue
        if biz.lower() in seen_biz:
            continue
        dom = _domain(web)
        if dom and dom in seen_dom:
            continue
        seen_biz.add(biz.lower())
        if dom:
            seen_dom.add(dom)
        em = str(f.get("email", "")).strip()
        if em.lower() in ("none", "n/a", "-"):
            em = ""
        new.append({"business": biz, "website": web, "email": em,
                    "location": f.get("location", ""),
                    "why_fit": f.get("why_fit", ""), "contacted": False})

    if dry:
        print(f"Found {len(found)}, {len(new)} new:")
        for l in new:
            print(f"  • {l['business']} ({l['location']}) — {l['website']}")
            print(f"    📧 {l['email'] or 'NO EMAIL FOUND'}  |  {l['why_fit']}")
        return 0

    if not new:
        msg = "🔎 <b>Piontrix scout</b> — searched the Royersford–KOP area, no NEW leads (all already queued/contacted)."
        print(msg)
        _tg(msg)
        return 0

    leads.extend(new)
    LEADS_FILE.write_text(json.dumps(leads, ensure_ascii=False, indent=2))

    lines = [f"🔎 <b>Piontrix scout — {len(new)} new local leads queued</b>",
             "<i>Royersford ↔ KOP · outreach will draft these next</i>", ""]
    for l in new:
        site = l["website"] if l["website"].lower() != "none" else "no website"
        lines.append(f"• <b>{l['business']}</b> ({l['location']})")
        lines.append(f"  📧 {l['email'] or '⚠️ no email'} · {site}")
        lines.append(f"  <i>{l['why_fit']}</i>")
    _tg("\n".join(lines))
    print(f"Queued {len(new)} new leads.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"PIONTRIX_SCOUT FAILED: {e}", file=sys.stderr)
        sys.exit(1)
