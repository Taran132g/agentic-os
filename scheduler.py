"""
Scheduled Tasks — run agent tasks on a recurring schedule.
Fires due tasks every 60 seconds. Stores schedule state in schedules.json.
"""

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from tools.atomic_state import read_json, write_json

log = logging.getLogger(__name__)

SCHEDULE_FILE = Path(__file__).parent / "schedules.json"

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class ScheduledTask:
    id: str
    name: str
    text: str
    agent: str           # career / personal / general
    schedule_type: str   # daily / weekly / hourly / once
    schedule_time: str   # "08:00" / "monday 08:00" / ISO datetime
    enabled: bool = True
    last_run: str = ""
    next_run: str = ""
    created: str = ""


def load_schedules() -> list[ScheduledTask]:
    raw = read_json(SCHEDULE_FILE, [])
    if not isinstance(raw, list):
        return []
    try:
        return [ScheduledTask(**t) for t in raw]
    except Exception as e:
        log.warning("Failed to parse schedules: %s", e)
        return []


def save_schedules(tasks: list[ScheduledTask]):
    try:
        write_json(SCHEDULE_FILE, [asdict(t) for t in tasks])
    except Exception as e:
        log.warning("Failed to save schedules: %s", e)


def create_schedule(name: str, text: str, agent: str,
                    schedule_type: str, schedule_time: str) -> ScheduledTask:
    now = datetime.now()
    task = ScheduledTask(
        id=str(uuid.uuid4())[:8],
        name=name, text=text, agent=agent,
        schedule_type=schedule_type, schedule_time=schedule_time,
        created=now.isoformat(),
    )
    task.next_run = _calc_next_run(task, now).isoformat()
    tasks = load_schedules()
    tasks.append(task)
    save_schedules(tasks)
    return task


def delete_schedule(task_id: str) -> bool:
    tasks = load_schedules()
    filtered = [t for t in tasks if t.id != task_id]
    if len(filtered) < len(tasks):
        save_schedules(filtered)
        return True
    return False


def toggle_schedule(task_id: str, enabled: bool) -> bool:
    tasks = load_schedules()
    for t in tasks:
        if t.id == task_id:
            t.enabled = enabled
            save_schedules(tasks)
            return True
    return False


def _parse_time(time_str: str) -> tuple[int, int] | None:
    try:
        h, m = map(int, time_str.split(":"))
        return h, m
    except Exception:
        return None


def _is_due(task: ScheduledTask, now: datetime) -> bool:
    if not task.enabled:
        return False

    stype = task.schedule_type
    stime = task.schedule_time

    if stype == "once":
        if task.last_run:
            return False
        try:
            return now >= datetime.fromisoformat(stime)
        except Exception:
            return False

    if stype == "hourly":
        if not task.last_run:
            return True
        return (now - datetime.fromisoformat(task.last_run)).total_seconds() >= 3600

    if stype == "daily":
        parsed = _parse_time(stime)
        if not parsed:
            return False
        h, m = parsed
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < target:
            return False
        if not task.last_run:
            return True
        return datetime.fromisoformat(task.last_run).date() < now.date()

    if stype == "weekly":
        parts = stime.lower().split()
        if len(parts) != 2:
            return False
        day_part, time_part = parts
        target_day = WEEKDAYS.get(day_part)
        if target_day is None:
            try:
                target_day = int(day_part)
            except Exception:
                return False
        if now.weekday() != target_day:
            return False
        parsed = _parse_time(time_part)
        if not parsed:
            return False
        h, m = parsed
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < target:
            return False
        if not task.last_run:
            return True
        return (now - datetime.fromisoformat(task.last_run)).days >= 7

    return False


def _calc_next_run(task: ScheduledTask, now: datetime) -> datetime:
    stype = task.schedule_type
    stime = task.schedule_time

    if stype == "once":
        try:
            return datetime.fromisoformat(stime)
        except Exception:
            return now + timedelta(days=1)

    if stype == "hourly":
        return now + timedelta(hours=1)

    if stype == "daily":
        parsed = _parse_time(stime)
        if not parsed:
            return now + timedelta(days=1)
        h, m = parsed
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    if stype == "weekly":
        parts = stime.lower().split()
        if len(parts) != 2:
            return now + timedelta(weeks=1)
        day_part, time_part = parts
        target_day = WEEKDAYS.get(day_part)
        if target_day is None:
            try:
                target_day = int(day_part)
            except Exception:
                return now + timedelta(weeks=1)
        parsed = _parse_time(time_part)
        if not parsed:
            return now + timedelta(weeks=1)
        h, m = parsed
        days_ahead = (target_day - now.weekday()) % 7 or 7
        return (now + timedelta(days=days_ahead)).replace(hour=h, minute=m, second=0, microsecond=0)

    return now + timedelta(hours=1)


def schedule_to_dict(t: ScheduledTask) -> dict:
    d = asdict(t)
    d["next_run_display"] = ""
    if t.next_run:
        try:
            nxt = datetime.fromisoformat(t.next_run)
            now = datetime.now()
            diff = nxt - now
            if diff.total_seconds() < 0:
                d["next_run_display"] = "Overdue"
            elif diff.total_seconds() < 3600:
                mins = int(diff.total_seconds() / 60)
                d["next_run_display"] = f"in {mins}m"
            elif diff.total_seconds() < 86400:
                hrs = int(diff.total_seconds() / 3600)
                d["next_run_display"] = f"in {hrs}h"
            else:
                d["next_run_display"] = nxt.strftime("%b %d %H:%M")
        except Exception:
            pass
    return d


async def run_scheduler(dispatch_fn):
    """Background loop — check every 60 s and fire due tasks."""
    log.info("[scheduler] Started.")
    while True:
        await asyncio.sleep(60)
        try:
            tasks = load_schedules()
            now = datetime.now()
            updates: dict[str, tuple[str, str]] = {}   # id -> (last_run, next_run)
            fired_once: list[str] = []
            for task in tasks:
                if _is_due(task, now):
                    log.info("[scheduler] Firing: %s (%s)", task.name, task.id)
                    await dispatch_fn(task)            # yield point — API edits can land here
                    updates[task.id] = (now.isoformat(), _calc_next_run(task, now).isoformat())
                    # One-shot schedules are spent once fired — drop them so
                    # they don't linger in the list showing "Overdue" forever.
                    if task.schedule_type == "once":
                        fired_once.append(task.id)
            if updates or fired_once:
                # Re-load to MERGE any schedules the API created/deleted during the
                # dispatch await, instead of saving back the stale pre-await list
                # (which would silently clobber a concurrent create/delete).
                merged: list[ScheduledTask] = []
                for t in load_schedules():
                    if t.id in fired_once:
                        continue
                    if t.id in updates:
                        t.last_run, t.next_run = updates[t.id]
                    merged.append(t)
                save_schedules(merged)
        except Exception as e:
            log.exception("[scheduler] Error: %s", e)
