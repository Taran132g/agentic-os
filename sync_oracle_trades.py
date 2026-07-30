#!/usr/bin/env python3.11
"""
Sync executor-placed trades from the Oracle listener into the Mac's trades.json,
so they appear on the trader dashboard.

The Dr. Profit executor runs 24/7 on Oracle and records paper trades to Oracle's
trades.json; the dashboards (local /trades and getpais.company) read the MAC's file.
This mirrors Oracle's executor trades (source dr_profit_paper / dr_profit_auto) into
the Mac tracker each run — idempotent, and it never touches the backfilled book,
SPX, or any manual/real trades on the Mac.

Runs on the Mac (needs SSH to Oracle). Safe to run on a timer.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAC_TRADES = Path.home() / "agentic_os" / "trades.json"
ORACLE = "ubuntu@129.159.182.210"
SSH_KEY = str(Path.home() / ".ssh" / "oracle_pais.key")
ORACLE_TRADES = "/home/ubuntu/agentic-os/trades.json"
SYNC_SOURCES = ("dr_profit_paper", "dr_profit_auto")   # trades the executor places


def _oracle_trades():
    try:
        r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=15", "-o",
                            "BatchMode=yes", ORACLE, f"cat {ORACLE_TRADES}"],
                           capture_output=True, text=True, timeout=40)
    except Exception as e:
        print("oracle ssh failed:", e)
        return None
    if r.returncode != 0:
        print("oracle read failed:", r.stderr[:200])
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print("oracle trades.json parse failed:", e)
        return None


def _atomic_write(path: Path, data: dict):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    od = _oracle_trades()
    if od is None:
        sys.exit(1)
    try:
        md = json.loads(MAC_TRADES.read_text())
    except Exception as e:
        print("mac trades.json unreadable:", e)
        sys.exit(1)

    # Drop previously-synced executor trades, then mirror Oracle's current set so
    # closes/updates propagate too. Non-executor trades (backfill, SPX, manual) stay.
    for bucket in ("active_trades", "closed_trades"):
        md[bucket] = [t for t in md.get(bucket, [])
                      if str(t.get("source", "")) not in SYNC_SOURCES]
    mirrored = 0
    for bucket in ("active_trades", "closed_trades"):
        for t in od.get(bucket, []):
            if str(t.get("source", "")) in SYNC_SOURCES:
                md.setdefault(bucket, []).append(t)
                mirrored += 1

    _atomic_write(MAC_TRADES, md)
    print(f"mirrored {mirrored} executor trade(s) from Oracle into the Mac tracker")


if __name__ == "__main__":
    main()
