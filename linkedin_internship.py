#!/usr/bin/env python3
"""LinkedIn internship outreach — daily drafting helper (2026-06-09).

Mirrors piontrix_outreach / brainscan_outreach, for Taran's **Summer 2027**
internship networking.

IMPORTANT: this does NOT search or scrape LinkedIn. Automating LinkedIn search/
connects violates LinkedIn's ToS and risks account restriction, and there is no
official API for connection requests. Instead it reads `linkedin_targets.json` —
people YOU found via LinkedIn's own filters (Company -> People -> School:
Pennsylvania State University) — and each day drafts ONE personalized connection
note + a post-accept message, to Telegram for review. You click Connect by hand.

    python3 linkedin_internship.py        # draft the next pending target (1/day)
Env: OUTREACH_DRY=1 (print only, don't consume queue), OUTREACH_LIMIT=1
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import which

from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")

# Reuse Piontrix's Telegram plumbing (same creds/env). No Gmail — LinkedIn sends
# are manual (there's no compliant API for them).
from piontrix_outreach import _tg_text  # noqa: E402

TARGETS_FILE = AGENTIC_DIR / "linkedin_targets.json"


def _first(name: str) -> str:
    return (name or "there").split()[0] if name else "there"


def _fallback(t: dict) -> tuple[str, str]:
    name, company, role = t.get("name", ""), t.get("company", ""), t.get("role", "")
    ctx = t.get("context", "")
    if t.get("alum"):
        connect = (f"Hi {_first(name)} — fellow Penn Stater (AI Eng '27). Admire your work as "
                   f"{role or 'an engineer'} at {company or 'your company'}. Learning from people doing "
                   "what I'm aiming for — would love to connect.")
    else:
        connect = (f"Hi {_first(name)} — AI Engineering student at Penn State targeting a Summer '27 "
                   f"internship. {('Loved ' + ctx + '. ') if ctx else ''}Would value connecting and "
                   "learning from your path.")
    followup = (f"Thanks for connecting, {_first(name)}! I'm a Penn State AI Eng student aiming for "
                f"Summer '27 internships and genuinely trying to learn how people broke into "
                f"{company or 'your company'}{(' / ' + role) if role else ''}. Would you be open to a quick "
                "15-min chat about your path? Totally get it if you're slammed — even a tip or two would "
                "mean a lot.")
    return connect[:280], followup


try:
    from tools.persona import persona_block
except Exception:                       # standalone/odd-cwd runs: no-op
    def persona_block() -> str:
        return ""


def _draft(t: dict) -> tuple[str, str]:
    """(connect_note, followup) via the `claude` CLI; falls back to templates."""
    fb = _fallback(t)
    if not which("claude"):
        return fb
    prompt = f"""You are helping Taranveer Singh, a Penn State AI Engineering student (class of 2027),
network on LinkedIn to land a SUMMER 2027 software/ML internship. He has shipped real projects:
BrainScan (open-source AI that reads your notes into a profile) and PAIS (an autonomous agent).

Person to reach out to:
  Name: {t.get('name')}
  Company: {t.get('company')}
  Role: {t.get('role')}
  Penn State alum: {t.get('alum')}
  Context / recent post: {t.get('context') or 'n/a'}

Produce TWO things:
1) CONNECT — a LinkedIn connection-request note UNDER 200 characters. Warm and specific; reference the
   alum tie or their work/post. NEVER ask for a job or referral; the only goal is to get the "Accept".
2) FOLLOWUP — a 3-4 line message to send AFTER they accept: conversational, curiosity-first (ask to learn
   how they broke into {t.get('company')}), and request a 15-minute informational chat. No hard ask.

{persona_block()}
Output EXACTLY this:
CONNECT: <note under 200 chars>
FOLLOWUP: <message>"""
    try:
        out = (subprocess.run(["claude", "-p", prompt], capture_output=True,
                              text=True, timeout=120).stdout or "").strip()
    except Exception:
        out = ""
    if "CONNECT:" not in out or "FOLLOWUP:" not in out:
        return fb
    connect = out.split("CONNECT:", 1)[1].split("FOLLOWUP:", 1)[0].strip()
    followup = out.split("FOLLOWUP:", 1)[1].strip()
    if not connect or not followup:
        return fb
    return connect[:280], followup


def process(t: dict) -> dict:
    connect, followup = _draft(t)
    name = t.get("name", "?")
    url = t.get("profile_url", "")
    block = (
        f"🔗 <b>LinkedIn — {name}</b>{' · Penn State alum' if t.get('alum') else ''}\n"
        f"🏢 {t.get('role', '')} @ {t.get('company', '')}\n"
        f"{url}\n\n"
        f"<b>1) Connection note ({len(connect)} chars):</b>\n{connect}\n\n"
        f"<b>2) After they accept:</b>\n{followup}\n\n"
        f"<i>Open their profile → Connect → Add a note → paste #1. Keep #2 for after they accept.</i>"
    )
    if os.environ.get("OUTREACH_DRY") == "1":
        print(block)
        return {"name": name, "mode": "dry"}
    _tg_text(block)
    return {"name": name, "mode": "review"}


def _ready(targets: list) -> list:
    # A row is draftable only once it has a real name + profile_url (so blank
    # per-company scaffold rows are skipped until you paste someone in).
    return [t for t in targets if not t.get("contacted") and t.get("name") and t.get("profile_url")]


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    dry = os.environ.get("OUTREACH_DRY") == "1"

    if not TARGETS_FILE.exists():
        print(f'No targets file at {TARGETS_FILE}. Add a JSON list of '
              '{"name","company","role","alum","profile_url","context"} — people you found via '
              "LinkedIn (Company -> People -> School: Pennsylvania State University).")
        return 0
    targets = json.loads(TARGETS_FILE.read_text())
    ready = _ready(targets)

    # Weekly "top up your queue" nudge.
    if mode == "reminder":
        scaffolds = sum(1 for t in targets if not t.get("contacted") and not t.get("name"))
        msg = (f"📌 <b>LinkedIn queue</b> — {len(ready)} person(s) ready to draft"
               f"{f', {scaffolds} blank company rows to fill' if scaffolds else ''}.\n"
               "Top up: Company → People → filter School = Pennsylvania State University, "
               "paste name + profile URL into linkedin_targets.json.")
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    limit = int(os.environ.get("OUTREACH_LIMIT", "1"))  # one per day by default
    if not ready:
        unfilled = sum(1 for t in targets if not t.get("contacted") and not t.get("name"))
        done = sum(1 for t in targets if t.get("contacted"))
        msg = (f"📭 <b>LinkedIn internship outreach</b> — nothing ready ({done} done"
               f"{f', {unfilled} blank rows to fill in' if unfilled else ''}). "
               f"Paste a name + profile URL into linkedin_targets.json to queue the next one.")
        print(msg)
        if not dry:
            _tg_text(msg)
        return 0

    pending = ready
    results = []
    for t in pending[:limit]:
        r = process(t)
        if not dry:  # a dry run is a preview — don't consume the daily queue
            t["contacted"] = True
        results.append(r)
    if not dry:
        TARGETS_FILE.write_text(json.dumps(targets, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"LINKEDIN OUTREACH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
