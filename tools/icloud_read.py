"""icloud_read.py — read a vault file that may be an iCloud dataless placeholder.

The Obsidian vault lives in iCloud Drive. iCloud can evict a synced note to the
cloud (a "dataless" placeholder); reading it then can raise OSError instead of
transparently downloading — most often EDEADLK ("Resource deadlock avoided",
errno 11 on macOS) when the file is mid-sync. A plain Path.read_text() therefore
fails intermittently, which is what made the bridge return empty pipelines and
the dashboard show 0 across the board.

read_text() nudges iCloud to materialize the file (stat + `brctl download`) and
retries with backoff, so a cold/mid-sync note resolves instead of crashing the
reader. Every vault-note reader (job_sheet, linkedin_sheet, sales) goes through
this so the behavior is identical on both codepaths.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

_MAX_ATTEMPTS = 4


def read_text(path) -> str:
    """Read a vault file, tolerating an iCloud-evicted (dataless) placeholder."""
    p = Path(path)
    last: OSError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return p.read_text(encoding="utf-8")
        except OSError as e:
            last = e
            try:                                   # ask iCloud to materialize, then back off
                p.stat()
                subprocess.run(["brctl", "download", str(p)],
                               capture_output=True, timeout=20)
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"vault file unreadable (iCloud not materialized): "
                       f"{p.name}: {last}")
