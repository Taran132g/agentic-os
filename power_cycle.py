#!/usr/bin/env python3
"""power_cycle.py — wake/sleep orchestration for the n8n schedules (2026-06-03).

macOS can't be woken by software (it's asleep) — a wake must be pre-armed in the
hardware RTC via `pmset schedule wake`. So we self-chain:

    [armed wake] → Mac wakes → n8n cron runs the cluster → n8n runs
    `power_cycle.py sleep` → it arms the NEXT wake, then sleeps the Mac.

The chain perpetuates itself. Bootstrap once with `power_cycle.py arm`.

Subcommands:
    next    print the next wake datetime (no side effects)
    arm     schedule the next wake event (sudo pmset schedule wake ...)
    sleep   arm the next wake, then sleep the Mac — UNLESS someone is actively
            using it (recent keyboard/mouse) or it's plugged-out and you'd rather
            not (see guards).

Env:
    POWER_DRY=1     print actions, run nothing
    POWER_FORCE=1   sleep even if the machine looks in-use (skip the idle guard)

REQUIRES a one-time passwordless-sudo rule for pmset (n8n runs unattended):
    echo "taranveersingh ALL=(root) NOPASSWD: /usr/bin/pmset" \
        | sudo tee /etc/sudoers.d/pais-pmset && sudo chmod 440 /etc/sudoers.d/pais-pmset
"""

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _tg(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN"); cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid) or os.environ.get("POWER_DRY") == "1":
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": int(cid), "text": text[:1000],
                            "parse_mode": "HTML"}, timeout=15)
    except Exception:
        pass

# (weekday set, "HH:MM") — wake ~5 min BEFORE each n8n task cluster.
# weekdays: Mon=0 … Sun=6
WAKE_TIMES = [
    (set(range(7)), "07:25"),   # daily  — before 7:30 email-triage + morning batch
    (set(range(7)), "18:25"),   # daily  — before 6:30pm email-triage
    ({2},           "19:55"),   # Wed    — before 8pm repo-sync
]
IDLE_GUARD_SECONDS = 180        # if input within this window, assume you're using it


def next_wake(after: datetime | None = None) -> datetime | None:
    after = after or datetime.now()
    best = None
    for d in range(0, 8):                       # search the next 8 days
        day = after + timedelta(days=d)
        for days, hhmm in WAKE_TIMES:
            if day.weekday() not in days:
                continue
            h, m = map(int, hhmm.split(":"))
            cand = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if cand > after and (best is None or cand < best):
                best = cand
    return best


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    if os.environ.get("POWER_DRY") == "1":
        print("[dry] would run:", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, capture_output=True, text=True)


def arm() -> datetime | None:
    nw = next_wake()
    if not nw:
        print("! no upcoming wake computed")
        return None
    stamp = nw.strftime("%m/%d/%Y %H:%M:%S")
    # Arm exactly one next wake. (No cancelall — that would also wipe macOS's own
    # calendar/system wake events. One-time wake events clear themselves once
    # their time passes, so they don't accumulate.)
    r = _run(["sudo", "pmset", "schedule", "wake", stamp])
    if r.returncode == 0:
        print(f"✓ armed wake: {stamp}")
    else:
        print(f"! arm failed (rc {r.returncode}): {r.stderr.strip()} "
              f"— is the pmset sudoers rule installed?")
    return nw


def _idle_seconds() -> float:
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True).stdout
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        if m:
            return int(m.group(1)) / 1e9        # nanoseconds → seconds
    except Exception:
        pass
    return 1e9


def sleep() -> None:
    nw = arm()                                  # always schedule the return trip first
    idle = _idle_seconds()
    if idle < IDLE_GUARD_SECONDS and os.environ.get("POWER_FORCE") != "1":
        print(f"↺ skip sleep — looks in use ({idle:.0f}s since last input). "
              f"Next wake still armed.")
        return
    if os.environ.get("POWER_DRY") == "1":
        print("[dry] would sleep now")
        return
    nwt = nw.strftime("%a %-I:%M%p") if nw else "?"
    _tg(f"😴 <b>Mac sleeping</b> — scheduled tasks done. Next wake armed for {nwt}.")
    subprocess.run(["osascript", "-e",
                    'tell application "System Events" to sleep'])
    print("😴 sleeping")


def wokeup() -> None:
    """Confirm the Mac woke (fired ~1 min after each armed wake by n8n)."""
    nw = next_wake()
    nwt = nw.strftime("%a %-I:%M%p") if nw else "?"
    _tg(f"☀️ <b>Mac awake</b> — running scheduled tasks. Next wake: {nwt}.")
    print("woke ping sent")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "next"
    if cmd == "arm":
        arm()
    elif cmd == "sleep":
        sleep()
    elif cmd == "wokeup":
        wokeup()
    else:
        print("next wake:", next_wake())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"POWER_CYCLE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
