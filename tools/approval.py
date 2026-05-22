"""
Approval gate — file-based IPC for external approvals.
Routing decisions now use asyncio futures registered via register_route_future().
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

TMP_DIR = Path(__file__).parent.parent / "tmp"
REQUEST_FILE = TMP_DIR / "approval_request.json"
RESPONSE_FILE = TMP_DIR / "approval_response.json"

_send_approval_message = None
_route_futures: dict[str, asyncio.Future] = {}


def register_sender(fn):
    global _send_approval_message
    _send_approval_message = fn


def register_route_future(task_id: str, fut: asyncio.Future):
    _route_futures[task_id] = fut


def pending_ids() -> list[str]:
    if REQUEST_FILE.exists():
        try:
            data = json.loads(REQUEST_FILE.read_text())
            if data.get("status") == "pending":
                return [data["id"]]
        except Exception:
            pass
    return []


def resolve(request_id: str, status) -> bool:
    """Resolve a routing future or write a file-IPC response.
    Returns True only when an actual pending request matched."""
    # Resolve routing futures (used by Telegram callbacks)
    if request_id in _route_futures:
        fut = _route_futures.pop(request_id)
        if not fut.done():
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(fut.set_result, status)
            except RuntimeError:
                # No running loop in this thread — set directly (best effort)
                try:
                    fut.set_result(status)
                except Exception as e:
                    log.warning(f"Future resolve error: {e}")
                    return False
            except Exception as e:
                log.warning(f"Future resolve error: {e}")
                return False
        return True

    # Fall back to file-based IPC for regular approvals — only valid if a request is pending
    if not REQUEST_FILE.exists():
        return False
    try:
        data = json.loads(REQUEST_FILE.read_text())
        if data.get("id") != request_id:
            return False
    except Exception:
        return False
    TMP_DIR.mkdir(exist_ok=True)
    RESPONSE_FILE.write_text(json.dumps({"id": request_id, "status": status}))
    return True


async def watch_approvals():
    """Background task: polls REQUEST_FILE for approval requests from Claude subprocesses."""
    while True:
        await asyncio.sleep(1.0)
        if REQUEST_FILE.exists():
            try:
                data = json.loads(REQUEST_FILE.read_text())
                if data.get("status") == "pending":
                    data["status"] = "sent"
                    REQUEST_FILE.write_text(json.dumps(data))
                    if _send_approval_message:
                        await _send_approval_message(
                            action_id=data["id"],
                            text=data["details"],
                            action=data.get("action", "approve"),
                        )
            except Exception as e:
                log.warning(f"Watcher error: {e}")


async def ask(action: str, details: str, timeout: float = 600.0) -> bool:
    """Block until Taran approves/denies, or `timeout` seconds elapse.

    On timeout the request is cleared and treated as denied — otherwise the
    calling Claude subprocess would hang forever waiting on a response file
    that never arrives.
    """
    import time
    import uuid
    request_id = str(uuid.uuid4())[:8]
    REQUEST_FILE.write_text(json.dumps({
        "id": request_id, "action": action,
        "details": details, "status": "pending",
    }))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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

    # Timed out — clear the stale request and default to denied.
    log.warning("Approval %s timed out after %.0fs — defaulting to denied", request_id, timeout)
    REQUEST_FILE.unlink(missing_ok=True)
    return False
