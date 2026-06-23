#!/usr/bin/env python3
"""Daily vault digest — morning cron (built for n8n 2026-05-31).

Summarizes YESTERDAY's session log(s) + collects still-open follow-ups from the
last week of chats, writes a dated note under `Daily Digests/`, and Telegrams a
short version so Taran starts the day with context.

Run (from the n8n Execute Command node, morning cron):
    python3 ~/agentic_os/vault_digest.py

Env:
    VAULT_DIGEST_DRY=1   print the note to stdout, don't write vault / Telegram
    VAULT_DIGEST_DATE=YYYY-MM-DD   summarize a specific day (default: yesterday)

Uses the `claude` CLI for a tight narrative summary when available (subscription
-billed, no API key); falls back to a structured extract if it's not on PATH.
Exit 0 always — a quiet day is still a successful run.
"""

import html
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")

VAULT = (Path.home() / "Library/Mobile Documents/iCloud~md~obsidian"
         / "Documents/Digital Brain")
CHATS = VAULT / "Chats"
DIGESTS = VAULT / "Daily Digests"


def _send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("! TELEGRAM creds missing")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": int(chat_id), "text": text[:4000],
               "parse_mode": "HTML", "disable_web_page_preview": True}
    resp = requests.post(url, json=payload, timeout=15)
    if resp.ok:
        return
    # HTML parse errors (400) shouldn't lose the digest. Telegram only allows a
    # small tag whitelist; any stray "<...>" in the LLM summary trips it even
    # after escaping edge cases. Fall back to plain text so the message still
    # lands, stripping the tags we control.
    print("! Telegram HTML send failed:", resp.status_code, resp.text[:200],
          "— retrying as plain text")
    plain = re.sub(r"</?(b|i|u|s|code|pre|a)\b[^>]*>", "", text)
    plain = html.unescape(plain)
    resp2 = requests.post(
        url, json={"chat_id": int(chat_id), "text": plain[:4000],
                   "disable_web_page_preview": True}, timeout=15)
    if not resp2.ok:
        print("! Telegram plain-text retry also failed:",
              resp2.status_code, resp2.text[:200])


def _safe_read(path: Path, attempts: int = 4) -> str:
    """Read an iCloud-synced vault note, tolerating the transient file locks
    (OSError errno 11 'Resource deadlock avoided') that iCloud throws when it holds
    a note open at the moment we read. That lock crashed the whole digest mid-run
    (vault_digest.py:138, the 06-22 FAILED briefing). Retry with a short backoff,
    then degrade to empty — run the digest without that note rather than die."""
    for i in range(attempts):
        try:
            return path.read_text(errors="ignore")
        except FileNotFoundError:
            return ""
        except OSError:
            if i + 1 >= attempts:
                return ""
            time.sleep(0.5 * (i + 1))
    return ""


def _safe_write(path: Path, text: str, attempts: int = 4) -> bool:
    """Write a vault note through the same transient iCloud locks. Returns success."""
    for i in range(attempts):
        try:
            path.write_text(text)
            return True
        except OSError:
            if i + 1 >= attempts:
                return False
            time.sleep(0.5 * (i + 1))
    return False


def _open_followups(window_days: int = 7) -> list[str]:
    """Grab lines under a 'Follow-up' heading across recent chat notes."""
    items: list[str] = []
    if not CHATS.exists():
        return items
    cutoff = datetime.now() - timedelta(days=window_days)
    for f in sorted(CHATS.glob("20*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", f.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if d < cutoff:
            continue
        text = _safe_read(f)
        # capture bullet lines in any "Follow-up" / "Next steps" section
        for sec in re.split(r"\n#{1,6}\s", text):
            head = sec.splitlines()[0].lower() if sec.strip() else ""
            if "follow" in head or "next step" in head:
                for line in sec.splitlines()[1:]:
                    s = line.strip()
                    if s.startswith(("-", "*", "•")) and len(s) > 3:
                        items.append(f"{s.lstrip('-*• ').strip()}  ({m.group(1)})")
    # de-dupe, keep order
    seen, out = set(), []
    for it in items:
        key = it.split("  (")[0].lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


try:
    from tools.persona import persona_block
except Exception:                       # standalone/odd-cwd runs: no-op
    def persona_block() -> str:
        return ""


def _claude_summary(raw: str) -> str | None:
    """Tight 4-6 bullet summary via the claude CLI, or None if unavailable."""
    from shutil import which
    if not which("claude"):
        return None
    prompt = ("Summarize yesterday's work session below into 4-6 crisp bullets "
              "(what was done / decided), then a one-line 'Momentum:' note on "
              "where things stand. No preamble, no questions back — this is an "
              "unattended digest. If the note is sparse, say so in one line.\n"
              + persona_block() + "\n" + raw[:6000])
    try:
        res = subprocess.run(["claude", "-p", prompt], capture_output=True,
                             text=True, timeout=120)
        out = res.stdout.strip()
        return out or None
    except Exception:
        return None


def main() -> int:
    if os.environ.get("VAULT_DIGEST_DATE"):
        day = datetime.strptime(os.environ["VAULT_DIGEST_DATE"], "%Y-%m-%d")
    else:
        day = datetime.now() - timedelta(days=1)
    day_str = day.strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    note = CHATS / f"{day_str}.md"
    raw = _safe_read(note)

    # Substance check: count non-heading, non-empty content chars. The Stop hook
    # seeds an empty template, so only summarize via claude when there's real text.
    content_chars = sum(len(l.strip()) for l in raw.splitlines()
                        if l.strip() and not l.strip().startswith(("#", "*", "_")))

    body_summary = ""
    if raw.strip():
        if content_chars >= 200:
            body_summary = _claude_summary(raw) or ""
        if not body_summary:
            # structured fallback: first ~12 non-empty content lines
            picked = [l for l in raw.splitlines()
                      if l.strip() and not l.strip().startswith("#")][:12]
            body_summary = "\n".join(picked)

    followups = _open_followups()

    md = [f"# Daily Digest — {today_str}", "",
          f"*Auto-generated each morning. Summarizes {day_str}.*", "",
          "## Yesterday"]
    md.append(body_summary if body_summary.strip()
              else f"_No session note found for {day_str}._")
    md += ["", "## Open Follow-ups (last 7 days)"]
    if followups:
        md += [f"- [ ] {it}" for it in followups]
    else:
        md.append("_None tracked._")
    md += ["", f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_"]
    content = "\n".join(md)

    # Telegram short version. body_summary + followups are LLM/vault-generated,
    # so HTML-escape them — Telegram's HTML parse_mode 400s on any tag outside
    # its tiny whitelist (e.g. a stray "<footer>"). Only the <b>/<i> wrappers we
    # add ourselves are literal markup.
    tg = [f"<b>📓 Daily Digest — {today_str}</b>",
          f"<i>Recap of {day_str}</i>", ""]
    if body_summary.strip():
        tg.append(html.escape(body_summary[:1500]))
    else:
        tg.append("No session note yesterday.")
    if followups:
        tg += ["", f"<b>Open follow-ups ({len(followups)}):</b>"]
        tg += [f"• {html.escape(it.split('  (')[0])}" for it in followups[:8]]
    tg_msg = "\n".join(tg)

    if os.environ.get("VAULT_DIGEST_DRY") == "1":
        print(content)
        print("\n----- TELEGRAM -----\n")
        print(tg_msg)
        return 0

    DIGESTS.mkdir(parents=True, exist_ok=True)
    if not _safe_write(DIGESTS / f"{today_str}.md", content):
        print("WARN: could not write digest note (iCloud lock) — posting feed anyway",
              file=sys.stderr)
    _send_telegram(tg_msg)
    print(f"Vault digest written for {today_str} (recap of {day_str}); "
          f"{len(followups)} follow-ups.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"VAULT DIGEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
