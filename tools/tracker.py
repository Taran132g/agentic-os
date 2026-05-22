"""Application status tracker — persists all applications to applications.json."""
import json
import datetime
from pathlib import Path

TRACKER_FILE = Path(__file__).parent.parent / "applications.json"

VALID_STATUSES = {"pending", "applied", "interview", "rejected", "offer", "withdrawn"}


def load_applications() -> list[dict]:
    if not TRACKER_FILE.exists():
        return []
    try:
        return json.loads(TRACKER_FILE.read_text())
    except Exception:
        return []


def _save(apps: list[dict]):
    TRACKER_FILE.write_text(json.dumps(apps, ensure_ascii=False, indent=2))


def save_application(job: dict, status: str = "pending", platform: str = "generic") -> dict:
    """Save or update an application. Returns the saved entry."""
    apps = load_applications()
    job_id = job.get("id", "")
    for app in apps:
        if app.get("job_id") == job_id:
            app["status"] = status
            app["platform"] = platform
            app["last_updated"] = datetime.datetime.now().isoformat()
            _save(apps)
            return app
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
        "applied_at": datetime.datetime.now().isoformat(),
        "last_updated": datetime.datetime.now().isoformat(),
        "cover_hook": job.get("tailored", {}).get("hook", ""),
    }
    apps.append(entry)
    _save(apps)
    return entry


def update_status(app_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    apps = load_applications()
    for app in apps:
        if app.get("id") == app_id or app.get("job_id") == app_id:
            app["status"] = status
            app["last_updated"] = datetime.datetime.now().isoformat()
            _save(apps)
            return True
    return False


def get_stats() -> dict:
    apps = load_applications()
    stats = {"total": len(apps)}
    for s in VALID_STATUSES:
        stats[s] = sum(1 for a in apps if a.get("status") == s)
    return stats
