"""Application status tracker — persists all applications to applications.json.

Reads/writes go through tools.atomic_state so the tracker is safe against the
concurrent writers it actually has: the career worker (in-process), fill_scouted
(a separately dispatched process), and the /api/update_status handler.
"""
import datetime
from pathlib import Path

from tools.atomic_state import locked_update, read_json

TRACKER_FILE = Path(__file__).parent.parent / "applications.json"

VALID_STATUSES = {"pending", "applied", "interview", "rejected", "offer", "withdrawn"}


def load_applications() -> list[dict]:
    data = read_json(TRACKER_FILE, [])
    return data if isinstance(data, list) else []


def save_application(job: dict, status: str = "pending", platform: str = "generic") -> dict:
    """Save or update an application (cross-process-safe). Returns the saved entry."""
    job_id = job.get("id", "")
    now = datetime.datetime.now().isoformat()
    captured: dict = {}

    def _mutate(apps):
        if not isinstance(apps, list):
            apps = []
        for app in apps:
            if app.get("job_id") == job_id:
                app["status"] = status
                app["platform"] = platform
                app["last_updated"] = now
                captured["entry"] = app
                return apps
        entry = {
            "id": f"app_{int(datetime.datetime.now().timestamp() * 1000)}",
            "job_id": job_id,
            "company": job.get("company", ""),
            "role": job.get("role", ""),
            "url": job.get("url", ""),
            "location": job.get("location", ""),
            "match_score": job.get("match_score", 0),
            "platform": platform,
            "status": status,
            "applied_at": now,
            "last_updated": now,
            "cover_hook": job.get("tailored", {}).get("hook", ""),
        }
        apps.append(entry)
        captured["entry"] = entry
        return apps

    locked_update(TRACKER_FILE, _mutate, default=[])
    return captured["entry"]


def update_status(app_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    now = datetime.datetime.now().isoformat()
    hit: dict = {"ok": False}

    def _mutate(apps):
        if not isinstance(apps, list):
            apps = []
        for app in apps:
            if app.get("id") == app_id or app.get("job_id") == app_id:
                app["status"] = status
                app["last_updated"] = now
                hit["ok"] = True
                break
        return apps

    locked_update(TRACKER_FILE, _mutate, default=[])
    return hit["ok"]


def get_stats() -> dict:
    apps = load_applications()
    stats = {"total": len(apps)}
    for s in VALID_STATUSES:
        stats[s] = sum(1 for a in apps if a.get("status") == s)
    return stats
