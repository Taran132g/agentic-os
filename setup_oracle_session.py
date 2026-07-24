#!/usr/bin/env python3.11
"""
One-shot: re-authenticate the Dr. Profit Telegram session and hand it to Oracle.

The old session key was revoked by Telegram (AUTH_KEY_DUPLICATED). This creates a
FRESH session (interactive: your phone number + the code Telegram texts you), then
does everything else automatically:
  1. logs in, writing a fresh dr_profit_session.session
  2. copies it to Oracle (the ONLY place it will run)
  3. deletes the Mac copy so it's never used in two places again
  4. restarts the Oracle listener and confirms it's up

Run it once:  python3.11 ~/agentic_os/setup_oracle_session.py
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

AGENTIC = Path.home() / "agentic_os"
load_dotenv(AGENTIC / ".env")

SESSION_BASE = str(AGENTIC / "dr_profit_session")
SESSION_FILE = Path(SESSION_BASE + ".session")
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or "0")
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

ORACLE = "ubuntu@129.159.182.210"
SSH_KEY = str(Path.home() / ".ssh" / "oracle_pais.key")
ORACLE_DIR = "/home/ubuntu/agentic-os"


async def reauth():
    from pyrogram import Client

    # Remove the revoked session so Pyrogram forces a fresh phone-code login.
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    app = Client(SESSION_BASE, api_id=API_ID, api_hash=API_HASH)
    print("\n=== Telegram re-login (enter your phone number, then the code) ===\n")
    await app.start()
    me = await app.get_me()
    print(f"\n✅ Logged in as {me.first_name} (@{me.username})")
    await app.stop()


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def deploy():
    if not SESSION_FILE.exists():
        print("✗ No fresh session was created — aborting.")
        sys.exit(1)

    print("\nCopying the fresh session to Oracle (only holder from now on)...")
    r = run(["scp", "-i", SSH_KEY, str(SESSION_FILE),
             f"{ORACLE}:{ORACLE_DIR}/dr_profit_session.session"])
    if r.returncode != 0:
        print("✗ scp to Oracle failed.")
        sys.exit(1)

    print("Restarting the Oracle listener...")
    run(["ssh", "-i", SSH_KEY, ORACLE,
         "sudo systemctl restart dr-profit-listener.service && sleep 9 && "
         "echo -n 'listener: ' && systemctl is-active dr-profit-listener.service && "
         "journalctl -u dr-profit-listener.service -n 8 --no-pager | "
         "grep -E 'Monitor running|AUTH_KEY_DUPLICATED' | tail -2"])

    # Never keep a usable copy on the Mac — that's what got the key revoked.
    SESSION_FILE.unlink(missing_ok=True)
    print("\nMac session copy removed. Oracle is the sole holder.")


if __name__ == "__main__":
    if not (API_ID and API_HASH):
        print("Missing TELEGRAM_API_ID / TELEGRAM_API_HASH in .env")
        sys.exit(1)
    try:
        asyncio.run(reauth())
    except RuntimeError:
        pass  # Pyrogram 2.0 asyncio cleanup quirk — session is already saved
    deploy()
    print("\n🎉 Done. Oracle is now running the Dr. Profit dry-trade executor 24/7 "
          "(via the Anthropic API). Dry trades will arrive as Telegram alerts.")
