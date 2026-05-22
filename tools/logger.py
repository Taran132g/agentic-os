import datetime
from pathlib import Path

BASE_VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
HUB_DIR  = "PAIS Hub"
TASKS_DIR = f"{HUB_DIR}/Tasks"


def log_completed_task(task_name, description, status="COMPLETED", actions=None, task_type="general"):
    now       = datetime.datetime.now()
    date_str  = now.strftime("%Y-%m-%d")
    time_str  = now.strftime("%H-%M")       # used in filename (no colons — filesystem safe)
    time_disp = now.strftime("%H:%M")       # used in file content for display
    safe_name = "".join(c if c.isalnum() or c in " _-" else "" for c in task_name).strip()[:60]
    filename  = f"{date_str} {time_str} {safe_name}.md"
    task_path = BASE_VAULT / TASKS_DIR / filename

    content  = f"# {task_name}\n\n"
    content += f"## Requested: {date_str} {time_disp}\n\n"
    content += f"## Status: {status}\n\n"
    content += f"## Type: {task_type}\n\n"
    content += f"## Description\n{description}\n\n"
    if actions:
        content += "## Actions Taken\n"
        for action in actions:
            content += f"- {action}\n"
        content += "\n"
    content += "## Links\n- [[Index|Back to Hub]]\n"

    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(content, encoding="utf-8")

    index_path = BASE_VAULT / HUB_DIR / "Index.md"
    if index_path.exists():
        try:
            idx = index_path.read_text(encoding="utf-8")
            link = f"- [[{filename[:-3]}]]"
            if link not in idx and "## 🗂 Task Logs" in idx:
                index_path.write_text(
                    idx.replace("## 🗂 Task Logs", f"## 🗂 Task Logs\n{link}"),
                    encoding="utf-8",
                )
        except Exception as e:
            print(f"Failed to update index: {e}")

    return str(task_path)
