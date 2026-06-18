"""Atomic, optionally cross-process-locked JSON state I/O.

Every PAIS state file (schedules, usage, applications, job_queue, pipeline, ...)
should read/write through here so the parallel workers + separately-dispatched
subprocesses can't tear a write or clobber each other's read-modify-write cycle.

- ``write_json``  — temp file in the same dir + ``os.replace`` (atomic on one fs).
- ``read_json``   — never silently nukes a corrupt file; backs it up, returns default.
- ``locked_update`` — ``flock(<path>.lock)`` around read -> fn(data) -> write, for the
  cross-process read-modify-write case (e.g. job_queue.json is written by both the
  scheduled scout process and the webhook fill process).

This generalises the temp-file+rename pattern ``sales_agent._append_rows`` already
uses, so the rest of the codebase can stop doing raw ``write_text`` on shared state.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


def write_json(path: "str | Path", obj: Any, *, indent: int = 2) -> Path:
    """Atomically write ``obj`` as JSON to ``path`` (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name per-process so concurrent writers never share a temp file.
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)  # atomic same-filesystem rename
    return path


def read_json(path: "str | Path", default: Any = None) -> Any:
    """Load JSON from ``path``. Returns ``default`` if missing. On corrupt JSON,
    moves the bad file aside to ``<path>.corrupt`` (never a silent wipe) and
    returns ``default`` so a torn write can't erase state without a trace."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        try:
            bak = path.with_name(path.name + ".corrupt")
            os.replace(path, bak)
            log.warning("Corrupt JSON at %s — backed up to %s (%s)", path, bak, e)
        except OSError:
            log.warning("Corrupt JSON at %s and backup failed: %s", path, e)
        return default


@contextmanager
def _flock(path: Path):
    """Cross-process advisory exclusive lock on ``<path>.lock``. No-op on the
    (non-POSIX) platforms without fcntl — degrades to atomic-write-only."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def locked_update(path: "str | Path", fn: Callable[[Any], Any], *, default: Any = None) -> Any:
    """Cross-process-safe read-modify-write.

    Holds an exclusive lock for the WHOLE cycle so two writers can't interleave:
    ``fn(data)`` receives the current state (or ``default`` if missing/corrupt),
    returns the new state to persist; that value is written atomically and also
    returned to the caller.
    """
    path = Path(path)
    with _flock(path):
        data = read_json(path, default)
        new = fn(data)
        write_json(path, new)
        return new
