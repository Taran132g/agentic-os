import datetime
from pathlib import Path

BASE_VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
HUB_DIR = "Jarvis Hub"
TASKS_DIR = f"{HUB_DIR}/Tasks"

def log_completed_task(task_name, description, status="COMPLETED", actions=None):
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    safe_name = "".join([c if c.isalnum() or c in " _-" else "" for c in task_name]).strip()
    task_path = BASE_VAULT / TASKS_DIR / f"{date_str} {safe_name}.md"

    content = f"# {date_str} {task_name}\n\n"
    content += f"## Description\n{description}\n\n"
    content += f"## Status: {status}\n\n"
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
            index_content = index_path.read_text(encoding="utf-8")
            task_link = f"- [[{date_str} {task_name}]]"
            if task_link not in index_content and "## 🗂 Task Logs" in index_content:
                new_content = index_content.replace(
                    "## 🗂 Task Logs", f"## 🗂 Task Logs\n{task_link}"
                )
                index_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            print(f"Failed to update index: {e}")

    return str(task_path)
