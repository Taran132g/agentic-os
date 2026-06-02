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

import os
import re
import subprocess
import sys
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
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": int(chat_id), "text": text[:4000],
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )
    if not resp.ok:
        print("! Telegram failed:", resp.status_code, resp.text[:200])


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
        text = f.read_text(errors="ignore")
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


def _claude_summary(raw: str) -> str | None:
    """Tight 4-6 bullet summary via the claude CLI, or None if unavailable."""
    from shutil import which
    if not which("claude"):
        return None
    prompt = ("Summarize yesterday's work session below into 4-6 crisp bullets "
              "(what was done / decided), then a one-line 'Momentum:' note on "
              "where things stand. No preamble, no questions back — this is an "
              "unattended digest. If the note is sparse, say so in one line.\n\n"
              + raw[:6000])
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
    raw = note.read_text(errors="ignore") if note.exists() else ""

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

    # Telegram short version
    tg = [f"<b>📓 Daily Digest — {today_str}</b>",
          f"<i>Recap of {day_str}</i>", ""]
    if body_summary.strip():
        tg.append(body_summary[:1500])
    else:
        tg.append("No session note yesterday.")
    if followups:
        tg += ["", f"<b>Open follow-ups ({len(followups)}):</b>"]
        tg += [f"• {it.split('  (')[0]}" for it in followups[:8]]
    tg_msg = "\n".join(tg)

    if os.environ.get("VAULT_DIGEST_DRY") == "1":
        print(content)
        print("\n----- TELEGRAM -----\n")
        print(tg_msg)
        return 0

    DIGESTS.mkdir(parents=True, exist_ok=True)
    (DIGESTS / f"{today_str}.md").write_text(content)
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
