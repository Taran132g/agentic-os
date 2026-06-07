#!/usr/bin/env python3
"""Piontrix outreach pipeline — built for n8n (2026-05-31).

Piontrix = Taran's student-run (Penn State / PA) tech-consulting company:
web + mobile dev, UI/UX, Salesforce, SEO, automation, conversion work for
small businesses and student founders. Voice = approachable, local,
value-first, NOT salesy; ends with a soft ask for a short call/meeting.

Per prospect it:
  1. Researches their website (fetch + strip → context for personalization)
  2. Drafts a tailored email in Taran's voice via the `claude` CLI
  3. Finds a contact email via Hunter.io domain-search
  4. Delivers — TWO modes:
       review (default) → Telegram Taran the draft + found email to approve
       send            → actually emails the prospect via Gmail (BCC Taran)

n8n (or any trigger) runs:
    python3 piontrix_outreach.py "<business>" "<website>"            # review
    python3 piontrix_outreach.py "<business>" "<website>" send       # really send
    python3 piontrix_outreach.py --batch                             # leads file, review

Env:
    OUTREACH_DRY=1   print everything, no Telegram / no send (test wiring)
Safety: cold email is irreversible — default is REVIEW. Sending requires the
explicit "send" arg (or send=true mapped to it by the workflow).
Exit 0 on success.
"""

import json
import os
import re
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from shutil import which
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")
LEADS_FILE = AGENTIC_DIR / "piontrix_leads.json"

SENDER_NAME = "Taranveer Singh"
SIGNATURE = (
    f"{SENDER_NAME}\n"
    "Founder, Piontrix — student-run tech consulting\n"
    "Penn State University · Pennsylvania"
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tg(method: str, **kw):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    try:
        return requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             timeout=20, **kw)
    except Exception as e:
        print("! telegram:", e)
        return None


def _tg_text(text: str) -> None:
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if cid:
        _tg("sendMessage", json={"chat_id": int(cid), "text": text[:4000],
                                 "parse_mode": "HTML", "disable_web_page_preview": True})


def _domain(website: str) -> str:
    w = website if "://" in website else "https://" + website
    host = urlparse(w).netloc or urlparse(w).path
    return host.replace("www.", "").strip("/").split("/")[0]


def _fetch_site(website: str) -> str:
    """Return a short text snapshot of the site for personalization context."""
    if not website or website.strip().lower() in ("none", "n/a", "-", ""):
        return "(no website — business is only on Facebook / aggregator pages)"
    url = website if "://" in website else "https://" + website
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 PiontrixBot"})
        html = r.text
    except Exception as e:
        return f"(could not fetch site: {e})"
    # crude tag strip
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    return (f"TITLE: {title}\n" if title else "") + text[:2500]


def _draft_email(business: str, website: str, site_text: str,
                 context: str = "") -> tuple[str, str]:
    """(subject, body) drafted by claude in Taran's Piontrix voice."""
    fallback_subject = f"A few website ideas for {business}"
    fallback_body = (
        f"Hi there,\n\n"
        f"My name is Taran and I run Piontrix, a student-run tech consulting team "
        f"here in Pennsylvania. I came across {business} and put together a couple "
        f"of quick ideas to improve your site — things like speed, design polish, "
        f"and turning more visitors into customers.\n\n"
        f"We work with small businesses and student founders to build and improve "
        f"websites and apps, usually at student-friendly rates. Would you be open "
        f"to a short 15-minute call this week to walk through what I found?\n\n"
        f"Best,\n{SIGNATURE}"
    )
    if not which("claude"):
        return fallback_subject, fallback_body

    prompt = f"""Write a short cold outreach email for Piontrix, a STUDENT-RUN tech
consulting company at Penn State (Pennsylvania). Piontrix helps small businesses
and student founders with websites, web/mobile apps, UI/UX, SEO, and automation.

Target business: {business}
Their website: {website}
What their site looks like (scraped):
{site_text[:1800]}
Why they likely need help (from local research): {context or "n/a"}

Write in Taran's voice: warm, local, student-run, genuinely helpful, NOT salesy
or corporate. CENTER the email on the specific PROFIT-BOOSTING opportunity in the
"Why they likely need help" note above — frame everything around helping them make
or keep more money. That opportunity may be online ordering (to cut delivery-app
commissions), online booking/automation, OR a sales/revenue/retention DASHBOARD
that shows them where their money is going. If the note mentions a dashboard or
analytics, NAME IT concretely and make it a highlight — e.g. "a simple dashboard
showing your busiest hours, average ticket, and which services bring the most
profit" — do not flatten the pitch down to just "a website" when the real value is
the data/automation. Reference 1-2 specific wins. Keep it ~120 words. End with a
soft ask for a short 15-minute call. Do NOT invent fake stats or numbers.

Output EXACTLY this format and nothing else:
SUBJECT: <one line>
<blank line>
<email body, ending with a sign-off line "Best," then nothing — the signature is
added automatically, so do not write a signature>"""
    try:
        res = subprocess.run(["claude", "-p", prompt], capture_output=True,
                             text=True, timeout=120)
        out = (res.stdout or "").strip()
    except Exception:
        out = ""
    if not out or "SUBJECT:" not in out:
        return fallback_subject, fallback_body
    subj_line, _, rest = out.partition("\n")
    subject = subj_line.replace("SUBJECT:", "").strip() or fallback_subject
    body = rest.strip()
    # ensure our signature is appended once
    if SENDER_NAME not in body:
        body = body.rstrip() + "\n" + SIGNATURE
    return subject, body


def _find_email(domain: str) -> dict:
    """Best contact via Hunter.io domain-search. Returns {email, name, conf} or {}."""
    key = os.environ.get("HUNTER_API_KEY")
    if not key or not domain:
        return {}
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": key, "limit": 10},
                         timeout=20)
        data = r.json().get("data", {})
    except Exception as e:
        print("! hunter:", e)
        return {}
    emails = data.get("emails", [])
    if not emails:
        return {}
    # prefer owner/founder/ceo/marketing, then highest confidence
    pref = ("owner", "founder", "ceo", "marketing", "manager")
    def score(e):
        dept = (e.get("position") or "") + " " + (e.get("department") or "")
        rank = next((i for i, p in enumerate(pref) if p in dept.lower()), len(pref))
        return (rank, -(e.get("confidence") or 0))
    best = sorted(emails, key=score)[0]
    name = " ".join(x for x in (best.get("first_name"), best.get("last_name")) if x)
    return {"email": best.get("value"), "name": name,
            "conf": best.get("confidence"), "position": best.get("position")}


def _send_gmail(to_email: str, subject: str, body: str) -> None:
    addr = os.environ["GMAIL_ADDRESS"]
    pw = os.environ["GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["From"] = f"{SENDER_NAME} <{addr}>"
    msg["To"] = to_email
    msg["Bcc"] = addr  # keep a copy for Taran
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(addr, pw)
        s.send_message(msg)


def _save_gmail_draft(subject: str, body: str, to_email: str = "") -> None:
    """Append the drafted email to Gmail's Drafts folder via IMAP (not sent)."""
    import imaplib
    import time
    from email.utils import formatdate
    addr = os.environ["GMAIL_ADDRESS"]; pw = os.environ["GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["From"] = f"{SENDER_NAME} <{addr}>"
    if to_email:
        msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        M.login(addr, pw)
        M.append('"[Gmail]/Drafts"', '(\\Draft)',
                 imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ── one prospect ──────────────────────────────────────────────────────────────

def process(business: str, website: str, mode: str, context: str = "",
            email: str = "") -> dict:
    domain = _domain(website)
    site_text = _fetch_site(website)
    subject, body = _draft_email(business, website, site_text, context)
    # Prefer the email the scout already found; fall back to Hunter (needs a domain).
    contact = {} if email else _find_email(domain)
    to_email = email or contact.get("email")

    if os.environ.get("OUTREACH_DRY") == "1":
        print(f"--- {business} ({domain}) ---")
        print("TO:", to_email or "(none found)", contact)
        print("SUBJECT:", subject)
        print(body)
        return {"business": business, "to": to_email, "mode": "dry"}

    # Save to Gmail Drafts (opt-in) so Taran can review/send from Gmail directly.
    gmail_draft = False
    if os.environ.get("OUTREACH_GMAIL_DRAFT") == "1":
        try:
            _save_gmail_draft(subject, body, to_email or "")
            gmail_draft = True
        except Exception as e:
            print("! gmail draft save failed:", e)

    # SEND mode — only with explicit opt-in AND a found address
    if mode == "send" and to_email:
        try:
            _send_gmail(to_email, subject, body)
            _tg_text(f"📨 <b>Piontrix outreach SENT</b>\n"
                     f"To: {contact.get('name') or ''} &lt;{to_email}&gt; ({business})\n"
                     f"Subj: {subject}")
            return {"business": business, "to": to_email, "mode": "sent"}
        except Exception as e:
            _tg_text(f"⚠️ <b>Piontrix send failed</b> for {business}: {e}")
            return {"business": business, "to": to_email, "mode": "send_failed", "error": str(e)}

    # REVIEW mode (default) — Telegram Taran the draft to approve
    who = f"{contact.get('name')} ({contact.get('position')})" if contact.get("name") else "—"
    conf = f" · {contact.get('conf')}% conf" if contact.get("conf") else ""
    gmail_note = "✅ Saved to Gmail Drafts\n" if gmail_draft else ""
    review = (
        f"📝 <b>Piontrix draft — {business}</b>\n"
        f"🌐 {domain or '(no website)'}\n"
        f"📧 <b>{to_email or 'NO EMAIL FOUND — reach out via Facebook/call'}</b>{conf}\n"
        f"👤 {who}\n{gmail_note}\n"
        f"<b>Subject:</b> {subject}\n\n"
        f"{body}\n\n"
        f"<i>Reply-ready. To auto-send, re-run with mode=send.</i>"
    )
    _tg_text(review)
    return {"business": business, "to": to_email, "mode": "review", "gmail_draft": gmail_draft}


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--batch":
        if not LEADS_FILE.exists():
            print(f"No leads file at {LEADS_FILE}. Create a JSON list of "
                  f'{{"business": "...", "website": "..."}}.')
            return 0
        leads = json.loads(LEADS_FILE.read_text())
        pending = [l for l in leads if not l.get("contacted")]
        limit = int(os.environ.get("OUTREACH_LIMIT", "5"))
        if not pending:
            # idle heartbeat — so a scheduled run is never silently a no-op
            done = sum(1 for l in leads if l.get("contacted"))
            msg = (f"📭 <b>Piontrix outreach</b> — ran, but no pending leads "
                   f"({done}/{len(leads)} already contacted). Add leads to "
                   f"piontrix_leads.json to queue more.")
            print(msg)
            if os.environ.get("OUTREACH_DRY") != "1":
                _tg_text(msg)
            return 0
        results = []
        for lead in pending[:limit]:
            r = process(lead["business"], lead["website"], "review",
                        context=" — ".join(x for x in (lead.get("location", ""),
                                                       lead.get("why_fit", "")) if x),
                        email=lead.get("email", ""))
            lead["contacted"] = True
            lead["last_result"] = r.get("mode")
            results.append(r)
        LEADS_FILE.write_text(json.dumps(leads, indent=2))
        print(json.dumps(results, indent=2))
        return 0

    if len(args) < 2:
        print('usage: piontrix_outreach.py "<business>" "<website>" [send|review]',
              file=sys.stderr)
        return 2
    business, website = args[0], args[1]
    mode = "send" if len(args) > 2 and args[2].lower() == "send" else "review"
    print(json.dumps(process(business, website, mode), indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"OUTREACH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
