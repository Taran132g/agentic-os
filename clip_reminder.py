#!/usr/bin/env python3
"""Send a clip-posting reminder to Telegram."""
import json
import os
import sys
import urllib.request
from pathlib import Path

# Load .env from same directory
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

tok  = os.environ.get("TELEGRAM_BOT_TOKEN")
chat = os.environ.get("TELEGRAM_CHAT_ID")

if not tok or not chat:
    print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    sys.exit(1)

slot = sys.argv[1] if len(sys.argv) > 1 else "now"
messages = {
    "9am":  "📱 Time to post 3 clips (9am slot) — open TikTok and fire them out!",
    "1pm":  "📱 Time to post 3 clips (1pm slot) — space them out then drop them!",
    "7pm":  "📱 Time to post 3 clips (7pm slot) — peak evening window, post now!",
}
text = messages.get(slot, f"📱 Post your clips now ({slot} slot)!")

req = urllib.request.Request(
    f"https://api.telegram.org/bot{tok}/sendMessage",
    data=json.dumps({"chat_id": chat, "text": text,
                     "disable_web_page_preview": True}).encode(),
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(req, timeout=20).read()
print(f"Sent: {text}")
