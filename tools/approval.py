"""Approval gate — file-based IPC for external approvals.

Each approval is isolated in its OWN pair of files under tmp/approvals/:
    <id>.req.json   the request   {id, action, details, status: pending|sent}
    <id>.resp.json  the response  {id, status: approved|denied|...}

Previously every approval shared one approval_request.json / approval_response.json,
so with the parallel workers a second approval clobbered the first's request file
and answers could be mis-routed or lost (a side-effect could proceed on the wrong
approval, or hang to a 600s timeout-deny). Per-id files remove that collision
entirely. Routing decisions still use asyncio futures via register_route_future().

scripts/approve.py (the subprocess-facing CLI) writes/polls the SAME per-id files.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

TMP_DIR = Path(__file__).parent.parent / "tmp"
APPROVALS_DIR = TMP_DIR / "approvals"

_send_approval_message = None
_route_futures: dict[str, asyncio.Future] = {}


def _req_path(rid: str) -> Path:
    return APPROVALS_DIR / f"{rid}.req.json"


def _resp_path(rid: str) -> Path:
    return APPROVALS_DIR / f"{rid}.resp.json"


def _write_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def register_sender(fn):
    global _send_approval_message
    _send_approval_message = fn


def register_route_future(task_id: str, fut: asyncio.Future):
    _route_futures[task_id] = fut


def pending_ids() -> list[str]:
    if not APPROVALS_DIR.exists():
        return []
    out = []
    for req in APPROVALS_DIR.glob("*.req.json"):
        try:
            data = json.loads(req.read_text())
            if data.get("status") in ("pending", "sent"):
                out.append(data["id"])
        except Exception:
            continue
    return out


def resolve(request_id: str, status) -> bool:
    """Resolve a routing future or write a file-IPC response for THIS id only.
    Returns True only when an actual pending request matched."""
    # Routing futures (used by Telegram routing callbacks)
    if request_id in _route_futures:
        fut = _route_futures.pop(request_id)
        if not fut.done():
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(fut.set_result, status)
            except RuntimeError:
                try:
                    fut.set_result(status)
                except Exception as e:
                    log.warning(f"Future resolve error: {e}")
                    return False
            except Exception as e:
                log.warning(f"Future resolve error: {e}")
                return False
        return True

    # File-IPC approval — only valid if THIS id's request is still present.
    if not _req_path(request_id).exists():
        return False
    _write_atomic(_resp_path(request_id), {"id": request_id, "status": status})
    return True


async def watch_approvals():
    """Background task: surfaces every pending per-id request from Claude subprocesses."""
    while True:
        await asyncio.sleep(1.0)
        if not APPROVALS_DIR.exists():
            continue
        for req in list(APPROVALS_DIR.glob("*.req.json")):
            try:
                data = json.loads(req.read_text())
                if data.get("status") != "pending":
                    continue
                data["status"] = "sent"
                _write_atomic(req, data)
                if _send_approval_message:
                    await _send_approval_message(
                        action_id=data["id"],
                        text=data["details"],
                        action=data.get("action", "approve"),
                    )
            except Exception as e:
                log.warning(f"Watcher error on {req.name}: {e}")


async def ask(action: str, details: str, timeout: float = 600.0) -> bool:
    """Block until Taran approves/denies, or `timeout` seconds elapse (then denied).

    Each call gets its own request/response files keyed by a unique id, so
    concurrent approvals never collide.
    """
    import time
    request_id = str(uuid.uuid4())[:8]
    req, resp = _req_path(request_id), _resp_path(request_id)
    resp.unlink(missing_ok=True)
    _write_atomic(req, {
        "id": request_id, "action": action,
        "details": details, "status": "pending",
    })
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        if not resp.exists():
            continue
        try:
            data = json.loads(resp.read_text())
            if data.get("id") == request_id:
                resp.unlink(missing_ok=True)
                req.unlink(missing_ok=True)
                return data.get("status") == "approved"
        except Exception:
            pass

    log.warning("Approval %s timed out after %.0fs — defaulting to denied", request_id, timeout)
    req.unlink(missing_ok=True)
    resp.unlink(missing_ok=True)
    return False
