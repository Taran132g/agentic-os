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


def resolve(request_id: str, status: str) -> bool:
    """Called by Telegram callback to write the response file."""
    TMP_DIR.mkdir(exist_ok=True)
    RESPONSE_FILE.write_text(json.dumps({
        "id": request_id,
        "status": status,
    }))
    return True


async def watch_approvals():
    """Background task in main.py that polls REQUEST_FILE for Claude's requests."""
    while True:
        await asyncio.sleep(1.0)
        if REQUEST_FILE.exists():
            try:
                data = json.loads(REQUEST_FILE.read_text())
                if data.get("status") == "pending":
                    # Mark as 'sent' so we don't spam
                    data["status"] = "sent"
                    REQUEST_FILE.write_text(json.dumps(data))
                    
                    if _send_approval_message:
                        # Call the registered sender with extra metadata
                        await _send_approval_message(
                            action_id=data["id"],
                            text=data["details"],
                            action=data.get("action", "approve")
                        )
            except Exception as e:
                log.warning(f"Watcher error: {e}")


async def ask(action: str, details: str) -> bool:
    """Internal async helper to prompt Taran and wait for response."""
    import uuid
    request_id = str(uuid.uuid4())[:8]
    
    # Write the request
    REQUEST_FILE.write_text(json.dumps({
        "id": request_id,
        "action": action,
        "details": details,
        "status": "pending",
    }))
    
    # Poll for response
    while True:
        await asyncio.sleep(0.5)
        if not RESPONSE_FILE.exists():
            continue
        try:
            data = json.loads(RESPONSE_FILE.read_text())
            if data.get("id") == request_id:
                RESPONSE_FILE.unlink(missing_ok=True)
                REQUEST_FILE.unlink(missing_ok=True)
                return data.get("status") == "approved"
        except Exception:
            pass


async def ask_routing(task: str) -> str:
    """Prompts Taran to choose a workflow for the task."""
    import uuid
    request_id = str(uuid.uuid4())[:8]
    
    # Write the request
    REQUEST_FILE.write_text(json.dumps({
        "id": request_id,
        "action": "route",
        "details": task,
        "status": "pending",
    }))
    
    # Poll for response
    while True:
        await asyncio.sleep(0.5)
        if not RESPONSE_FILE.exists():
            continue
        try:
            data = json.loads(RESPONSE_FILE.read_text())
            if data.get("id") == request_id:
                RESPONSE_FILE.unlink(missing_ok=True)
                REQUEST_FILE.unlink(missing_ok=True)
                return data.get("status") # e.g. 'career', 'personal', 'review', 'general'
        except Exception:
            pass
