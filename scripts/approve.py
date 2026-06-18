#!/usr/bin/env python3
"""
Blocking approval gate — called by Claude via Bash tool.
Writes a per-id request file and polls until the main Python process resolves it.

Each invocation uses its OWN tmp/approvals/<id>.req.json + <id>.resp.json, so
concurrent approvals from parallel agents never clobber each other (this mirrors
tools/approval.py — keep the two in sync).

Usage (in Claude's bash command):
    result=$(python3 ~/agentic_os/scripts/approve.py "Action label" "Full details")
    if [ "$result" = "denied" ]; then exit 0; fi
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

APPROVALS_DIR = Path(__file__).parent.parent / "tmp" / "approvals"
TIMEOUT_SECONDS = 600


def _write_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def main():
    if len(sys.argv) < 3:
        print("denied")
        sys.exit(1)

    action = sys.argv[1]
    details = sys.argv[2]
    request_id = str(uuid.uuid4())[:8]
    req = APPROVALS_DIR / f"{request_id}.req.json"
    resp = APPROVALS_DIR / f"{request_id}.resp.json"

    # Clear any stale response for this id, then write the request.
    resp.unlink(missing_ok=True)
    _write_atomic(req, {
        "id": request_id,
        "action": action,
        "details": details,
        "status": "pending",
    })

    # Poll for THIS id's response.
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(0.5)
        if not resp.exists():
            continue
        try:
            data = json.loads(resp.read_text())
            if data.get("id") == request_id:
                resp.unlink(missing_ok=True)
                req.unlink(missing_ok=True)
                print(data.get("status", "denied"))
                return
        except Exception:
            pass

    # Timed out — clear this id's request and default to denied.
    req.unlink(missing_ok=True)
    resp.unlink(missing_ok=True)
    print("denied")


if __name__ == "__main__":
    main()
