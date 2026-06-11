#!/usr/bin/env python3
"""Gmail triage — built for n8n (2026-06-01).

Pulls RECENT unread mail from Gmail (IMAP, app password), classifies each with
the `claude` CLI into category + priority + a one-line "what to do", then:
  - always → Telegrams Taran a prioritized digest (action-needed first)
  - act mode → labels everything `Triaged/<Category>` in Gmail, and archives +
    marks-read only the clear low-priority Promo/Newsletter mail (reversible —
    archive just removes the Inbox label; nothing is deleted)

Modes:
    python3 email_triage.py            # read-only digest (DEFAULT, safe)
    python3 email_triage.py act        # also label + archive clear promos

Env:
    TRIAGE_DRY=1     print everything, no Telegram, no Gmail changes
    TRIAGE_HOURS=24  lookback window (default 24h)
    TRIAGE_LIMIT=40  max messages to classify per run

Read-only mode uses BODY.PEEK and a readonly mailbox, so triage never marks
your mail as read. Exit 0 always.
"""

import email
import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path
from shutil import which

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")

CATEGORIES = ["Urgent/Action", "Personal", "Job/Career", "Finance",
              "Newsletter/Promo", "Notification", "Other"]
ARCHIVE_CATS = {"Newsletter/Promo", "Notification"}


def _tg_text(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN"); cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and cid):
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": int(cid), "text": text[:4090],
                          "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if not r.ok:
            # HTML parse error (e.g. an un-escaped <>& in a subject) → don't drop
            # the digest; resend as plain text (strip tags) so it always lands.
            print("! telegram HTML failed:", r.status_code, r.text[:120])
            plain = re.sub(r"</?[a-z][^>]*>", "", text)
            requests.post(url, json={"chat_id": int(cid), "text": plain[:4090],
                          "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        print("! telegram:", e)


def _decode(s) -> str:
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _snippet(msg) -> str:
    """Short plain-text snippet from an email.message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                    break
                except Exception:
                    continue
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            body = ""
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:400]


try:
    from tools.persona import persona_block
except Exception:                       # standalone/odd-cwd runs: no-op
    def persona_block() -> str:
        return ""


def _classify(items: list[dict]) -> list[dict]:
    """Batch-classify via claude → list of {category, priority, action} per item."""
    n = len(items)
    if not which("claude") or n == 0:
        # heuristic fallback
        out = []
        for it in items:
            frm = (it["from"] + " " + it["subject"]).lower()
            promo = any(k in frm for k in ("newsletter", "unsubscribe", "sale",
                        "% off", "deal", "promo", "noreply", "no-reply"))
            out.append({"category": "Newsletter/Promo" if promo else "Other",
                        "priority": 3 if promo else 2, "action": ""})
        return out

    listing = "\n".join(
        f'[{i}] FROM: {it["from"][:60]} | SUBJ: {it["subject"][:90]} | {it["snippet"][:160]}'
        for i, it in enumerate(items))
    prompt = f"""You are triaging Taran's Gmail inbox. Classify each email below.

Categories (pick ONE): {", ".join(CATEGORIES)}
Priority: 1 = needs Taran's attention soon, 2 = read when convenient, 3 = low/ignorable.
action: <=8 words on what to do, or "" if none. Be terse.
{persona_block()}
Emails:
{listing}

Return ONLY a JSON array of exactly {n} objects in order, each:
{{"category": "<one of the categories>", "priority": 1|2|3, "action": "<terse>"}}
No prose, no code fences."""
    try:
        res = subprocess.run(["claude", "-p", prompt], capture_output=True,
                             text=True, timeout=180)
        raw = (res.stdout or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        m = re.search(r"\[.*\]", raw, flags=re.S)
        data = json.loads(m.group(0) if m else raw)
        if isinstance(data, list) and len(data) == n:
            return data
    except Exception as e:
        print("! classify fallback:", e)
    return [{"category": "Other", "priority": 2, "action": ""} for _ in items]


def main() -> int:
    mode = "act" if (len(sys.argv) > 1 and sys.argv[1].lower() == "act") else "triage"
    dry = os.environ.get("TRIAGE_DRY") == "1"
    hours = int(os.environ.get("TRIAGE_HOURS", "24"))
    limit = int(os.environ.get("TRIAGE_LIMIT", "40"))

    addr = os.environ["GMAIL_ADDRESS"]; pw = os.environ["GMAIL_APP_PASSWORD"]
    readonly = (mode != "act") or dry
    # timeout: a Gmail stall must fail the run, not hang it forever (n8n would
    # otherwise hold the execution open indefinitely).
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    M.login(addr, pw)
    M.select("INBOX", readonly=readonly)

    since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
    # UID search (not sequence numbers): act mode stores on a SECOND connection
    # minutes later — sequence numbers aren't stable across sessions, UIDs are.
    typ, data = M.uid("search", None, f'(UNSEEN SINCE "{since}")')
    uids = data[0].split() if data and data[0] else []
    uids = uids[-limit:]  # most recent N

    # Fetch in ONE batched, bounded call — never pull whole raw bodies (huge +
    # crashes py3.14 imaplib on certain messages). Headers first (critical),
    # then a best-effort partial-text snippet that can fail without killing the run.
    items = []
    if uids:
        num_set = b",".join(uids)

        def _by_num(data):
            out = {}
            for part in data or []:
                if isinstance(part, tuple) and len(part) >= 2:
                    meta = part[0].decode(errors="replace")
                    m = re.search(r"UID (\d+)", meta)
                    if m:
                        out[m.group(1)] = part[1]
            return out

        try:
            typ, hdr = M.uid("fetch", num_set,
                             "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            hmap = _by_num(hdr)
        except Exception as e:
            print("! header fetch failed:", e)
            hmap = {}

        for num in uids:
            key = num.decode() if isinstance(num, bytes) else str(num)
            msg = email.message_from_bytes(hmap.get(key, b""))
            items.append({"num": num,
                          "from": _decode(msg.get("From")),
                          "subject": _decode(msg.get("Subject")),
                          "snippet": ""})

        # snippets — bounded to 400 bytes/part, fully optional
        try:
            typ, body = M.uid("fetch", num_set, "(BODY.PEEK[1]<0.400>)")
            smap = _by_num(body)
            for it in items:
                key = it["num"].decode() if isinstance(it["num"], bytes) else str(it["num"])
                raw = smap.get(key, b"").decode(errors="replace")
                it["snippet"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()[:300]
        except Exception as e:
            print("! snippet fetch skipped:", e)

    def _logout_safe(conn):
        try:
            conn.logout()
        except Exception:
            pass

    if not items:
        msg = f"📧 <b>Gmail triage</b> — no new unread in the last {hours}h. Inbox quiet ✨"
        print(msg)
        if not dry:
            _tg_text(msg)
        _logout_safe(M)
        return 0

    # Close IMAP BEFORE the slow claude classify — holding an idle Gmail socket
    # open across a multi-minute call makes Gmail drop it (logout → EOF crash).
    _logout_safe(M)

    cls = _classify(items)
    for it, c in zip(items, cls):
        it.update(category=c.get("category", "Other"),
                  priority=int(c.get("priority", 2) or 2),
                  action=c.get("action", ""))

    # ── act: reconnect fresh (first connection is closed) and label/archive ────
    # UID store: the UIDs from the first session stay valid here; sequence
    # numbers would not (mail arriving/expunged during classify shifts them).
    archived = 0
    act_fails = 0
    if mode == "act" and not dry:
        try:
            M2 = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
            M2.login(addr, pw)
            M2.select("INBOX", readonly=False)
            for it in items:
                cat = it["category"].replace(" ", "_").replace("/", "-")
                try:
                    typ, _ = M2.uid("store", it["num"], "+X-GM-LABELS",
                                    f'(Triaged/{cat})')
                    if typ != "OK":
                        raise RuntimeError(f"label store returned {typ}")
                except Exception as e:
                    act_fails += 1
                    print(f"! label failed for uid {it['num']}: {e}")
                if it["category"] in ARCHIVE_CATS and it["priority"] == 3:
                    try:
                        M2.uid("store", it["num"], "+FLAGS", "(\\Seen)")
                        # Gmail's system label is \Inbox — ONE backslash on the
                        # wire. Removing it archives (reversible, nothing deleted).
                        typ, _ = M2.uid("store", it["num"], "-X-GM-LABELS",
                                        "(\\Inbox)")
                        if typ != "OK":
                            raise RuntimeError(f"archive store returned {typ}")
                        archived += 1
                    except Exception as e:
                        act_fails += 1
                        print(f"! archive failed for uid {it['num']}: {e}")
            _logout_safe(M2)
        except Exception as e:
            act_fails += 1
            print("! act-mode labeling skipped:", e)

    # ── build digest ──────────────────────────────────────────────────────────
    import html
    esc = lambda s: html.escape(str(s))   # sender/subject contain <>& → would
    #                                       break Telegram HTML parse_mode (400)

    by_cat: dict[str, list] = {}
    for it in sorted(items, key=lambda x: x["priority"]):
        by_cat.setdefault(it["category"], []).append(it)

    action_needed = [it for it in items if it["priority"] == 1]
    head = [f"📧 <b>Gmail triage</b> — {len(items)} new (last {hours}h)",
            f"⚡ {len(action_needed)} need attention"
            + (f" · 🗄️ archived {archived} promos" if mode == "act" and not dry else "")
            + (f" · ⚠️ {act_fails} label/archive op(s) FAILED" if act_fails else "")]
    lines = head + [""]
    if action_needed:
        lines.append("<b>⚡ Needs attention</b>")
        for it in action_needed:
            act = f" — <i>{esc(it['action'])}</i>" if it["action"] else ""
            lines.append(f"• <b>{esc(it['from'][:35])}</b>: {esc(it['subject'][:60])}{act}")
        lines.append("")
    for cat in CATEGORIES:
        if cat == "Urgent/Action" or cat not in by_cat:
            continue
        msgs = by_cat[cat]
        lines.append(f"<b>{cat}</b> ({len(msgs)})")
        for it in msgs[:4]:
            lines.append(f"• {esc(it['from'][:30])}: {esc(it['subject'][:55])}")
        if len(msgs) > 4:
            lines.append(f"  …+{len(msgs)-4} more")
        lines.append("")
    if mode != "act":
        lines.append("<i>Read-only. Run with `act` to auto-label + archive promos.</i>")
    digest = "\n".join(lines)

    if dry:
        print(digest)
        print(f"\n[dry] mode={mode} would archive "
              f"{sum(1 for it in items if it['category'] in ARCHIVE_CATS and it['priority']==3)} promos")
        return 0

    _tg_text(digest)
    print(f"Triage done — {len(items)} classified, {archived} archived, mode={mode}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"TRIAGE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
