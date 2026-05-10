#!/usr/bin/env python3
"""
Blocking approval gate — called by Claude via Bash tool.
Writes a request file and polls until the main Python process resolves it.

Usage (in Claude's bash command):
    result=$(python3 ~/agentic_os/scripts/approve.py "Action label" "Full details")
    if [ "$result" = "denied" ]; then exit 0; fi
"""

import sys
import json
import time
import uuid
from pathlib import Path

TMP_DIR = Path(__file__).parent.parent / "tmp"
REQUEST_FILE = TMP_DIR / "approval_request.json"
RESPONSE_FILE = TMP_DIR / "approval_response.json"
TIMEOUT_SECONDS = 600


def main():
    if len(sys.argv) < 3:
        print("denied")
        sys.exit(1)

    action = sys.argv[1]
    details = sys.argv[2]
    request_id = str(uuid.uuid4())[:8]

    TMP_DIR.mkdir(exist_ok=True)

    # Clear any stale response
    RESPONSE_FILE.unlink(missing_ok=True)

    # Write the request
    REQUEST_FILE.write_text(json.dumps({
        "id": request_id,
        "action": action,
        "details": details,
        "status": "pending",
    }))

    # Poll for response
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(0.5)
        if not RESPONSE_FILE.exists():
            continue
        try:
            data = json.loads(RESPONSE_FILE.read_text())
            if data.get("id") == request_id:
                RESPONSE_FILE.unlink(missing_ok=True)
                REQUEST_FILE.unlink(missing_ok=True)
                print(data.get("status", "denied"))
                return
        except Exception:
            pass

    # Timed out
    REQUEST_FILE.unlink(missing_ok=True)
    print("denied")


if __name__ == "__main__":
    main()
