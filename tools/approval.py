"""
Approval gate — file-based IPC between Claude subprocess and Python process.
The watcher task in main.py polls the request file and resolves via Telegram.
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

TMP_DIR = Path(__file__).parent.parent / "tmp"
REQUEST_FILE = TMP_DIR / "approval_request.json"
RESPONSE_FILE = TMP_DIR / "approval_response.json"

_send_approval_message = None  # set by telegram_bot at startup


def register_sender(fn):
    global _send_approval_message
    _send_approval_message = fn


def pending_ids() -> list[str]:
    if REQUEST_FILE.exists():
        try:
            data = json.loads(REQUEST_FILE.read_text())
            if data.get("status") == "pending":
                return [data["id"]]
        except Exception:
            pass
    return []


def resolve(request_id: str, approved: bool) -> bool:
    """Called by Telegram callback to write the response file."""
    TMP_DIR.mkdir(exist_ok=True)
    RESPONSE_FILE.write_text(json.dumps({
        "id": request_id,
        "status": "approved" if approved else "denied",
    }))
    return True


async def watch_approvals():
    """Background task — polls for new approval requests and fires Telegram messages."""
    TMP_DIR.mkdir(exist_ok=True)
    seen_id = None

    while True:
        await asyncio.sleep(0.5)
        if not REQUEST_FILE.exists():
            continue
        try:
            data = json.loads(REQUEST_FILE.read_text())
        except Exception:
            continue

        if data.get("status") != "pending":
            continue
        if data.get("id") == seen_id:
            continue

        seen_id = data["id"]
        if _send_approval_message:
            await _send_approval_message(
                action_id=data["id"],
                text=f"*Action:* {data['action']}\n\n{data['details']}",
            )
        else:
            log.warning("No approval sender registered")
