#!/usr/bin/env python3
"""BrainScan creator outreach — n8n companion to piontrix_outreach (2026-06-05).

Reads a verified creator/newsletter contact list (brainscan_creators.json) and,
per PENDING contact, drafts a personalized email (BrainScan voice + that
contact's hook) and delivers it the same way Piontrix does:
    review (default) -> Telegram Taran the draft to approve
    send            -> actually email via Gmail (BCC Taran)
Opt-in OUTREACH_GMAIL_DRAFT=1 also saves each to Gmail Drafts.

Built to run right AFTER piontrix-outreach in n8n. Only touches entries that are
PENDING (no email is sent without a real, pre-verified address) and marks each
done after handling, so repeated daily runs never re-contact anyone.

    python3 brainscan_outreach.py            # batch, review (default)
    python3 brainscan_outreach.py send       # batch, actually send pending
Env: OUTREACH_DRY=1 (no TG/send), OUTREACH_GMAIL_DRAFT=1, OUTREACH_LIMIT=10
Safety: cold email is irreversible — default is REVIEW. Emails are NEVER guessed;
the list must contain real, pre-verified public contact addresses.
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

# Reuse Piontrix's Telegram + Gmail plumbing (same creds/env, DRY).
from piontrix_outreach import _tg_text, _send_gmail, _save_gmail_draft  # noqa: E402

CREATORS_FILE = AGENTIC_DIR / "brainscan_creators.json"
SENDER_NAME = "Taranveer Singh"
REPO = "https://github.com/Taran132g/brainscan"
SIGNATURE = f"{SENDER_NAME}\nBrainScan · {REPO}"


def _fallback(name: str, hook: str) -> tuple[str, str]:
    subject = "BrainScan — reads your Obsidian vault into a Brain Card"
    body = (
        f"Hi {name},\n\n"
        f"{hook}\n\n"
        "I built BrainScan: it reads your whole Obsidian/Notion vault and writes an honest "
        '"Brain Card" — how you think, what drives you, and how you connect. From your real '
        "notes, not a 20-question quiz. It's open source and privacy-first (your notes become "
        "vectors, never stored as raw text).\n\n"
        "I'd love to give you a free scan of your own vault, and I can set up free scans + a "
        "code for your audience if it's a fit.\n\n"
        f"30-second demo + repo: {REPO}\n\n"
        f"— {SIGNATURE}"
    )
    return subject, body


def _draft_email(name: str, hook: str) -> tuple[str, str]:
    """(subject, body) drafted by the `claude` CLI in BrainScan's voice."""
    fb = _fallback(name, hook)
    if not which("claude"):
        return fb
    prompt = f"""Write a short, warm cold-outreach email from Taranveer, founder of BrainScan,
to a PKM / Obsidian creator named {name}.

BrainScan: an open-source tool that reads someone's Obsidian/Notion vault and writes an honest,
whole-person "Brain Card" — how they think, what drives them, and how they connect. From their
real notes, not a quiz. Privacy-first (notes become vectors, never stored as raw text). $2 hosted
or free to self-host. Repo: {REPO}.

Personalize the opening around this specific hook about them: "{hook}".
Offer two things: (1) a free scan of their own vault (their reaction could be content), and
(2) free scans + a code for their audience if it's a fit. Genuine and specific, never salesy.
~110 words. End with a sign-off line "—" then nothing (the signature is added automatically, so
do NOT write a signature).

Output EXACTLY this and nothing else:
SUBJECT: <one line>
<blank line>
<email body>"""
    try:
        res = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=120)
        out = (res.stdout or "").strip()
    except Exception:
        out = ""
    if not out or "SUBJECT:" not in out:
        return fb
    subj_line, _, rest = out.partition("\n")
    subject = subj_line.replace("SUBJECT:", "").strip() or fb[0]
    body = rest.strip()
    if SENDER_NAME not in body:
        body = body.rstrip() + "\n" + SIGNATURE
    return subject, body


def process(c: dict, mode: str) -> dict:
    name = c.get("name", "there")
    email = c.get("email", "")
    hook = c.get("hook", "")
    subject, body = _draft_email(name, hook)

    if os.environ.get("OUTREACH_DRY") == "1":
        print(f"--- {name} <{email or '(no email)'}> ---")
        print("SUBJECT:", subject)
        print(body)
        return {"name": name, "to": email, "mode": "dry"}

    gmail_draft = False
    if os.environ.get("OUTREACH_GMAIL_DRAFT") == "1" and email:
        try:
            _save_gmail_draft(subject, body, email)
            gmail_draft = True
        except Exception as e:
            print("! gmail draft save failed:", e)

    if mode == "send" and email:
        try:
            _send_gmail(email, subject, body)
            _tg_text(f"📨 <b>BrainScan outreach SENT</b>\nTo: {name} &lt;{email}&gt;\nSubj: {subject}")
            return {"name": name, "to": email, "mode": "sent"}
        except Exception as e:
            _tg_text(f"⚠️ <b>BrainScan send failed</b> for {name}: {e}")
            return {"name": name, "to": email, "mode": "send_failed", "error": str(e)}

    note = "✅ Saved to Gmail Drafts\n" if gmail_draft else ""
    _tg_text(
        f"🧠 <b>BrainScan draft — {name}</b>\n"
        f"📧 <b>{email or 'NO EMAIL — skipped'}</b>\n{note}\n"
        f"<b>Subject:</b> {subject}\n\n{body}\n\n"
        f"<i>Reply-ready. To auto-send pending, re-run with arg: send</i>"
    )
    return {"name": name, "to": email, "mode": "review", "gmail_draft": gmail_draft}


def main() -> int:
    mode = "send" if (len(sys.argv) > 1 and sys.argv[1].lower() == "send") else "review"
    if not CREATORS_FILE.exists():
        print(f'No creators file at {CREATORS_FILE}. Add a JSON list of '
              '{"name","email","hook"} (real, pre-verified public emails only).')
        return 0
    creators = json.loads(CREATORS_FILE.read_text())
    pending = [c for c in creators if not c.get("contacted") and c.get("email")]
    limit = int(os.environ.get("OUTREACH_LIMIT", "10"))

    if not pending:
        done = sum(1 for c in creators if c.get("contacted"))
        msg = (f"📭 <b>BrainScan outreach</b> — ran, but no pending creators "
               f"({done}/{len(creators)} already handled). Add entries to "
               f"brainscan_creators.json to queue more.")
        print(msg)
        if os.environ.get("OUTREACH_DRY") != "1":
            _tg_text(msg)
        return 0

    results = []
    for c in pending[:limit]:
        r = process(c, mode)
        # Mark handled after review or send so it's never re-contacted on the next run.
        c["contacted"] = True
        c["last_result"] = r.get("mode")
        results.append(r)
    CREATORS_FILE.write_text(json.dumps(creators, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"OUTREACH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
