import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

VAULT_PATH = Path(
    os.environ.get(
        "VAULT_PATH",
        os.path.expanduser(
            "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
        ),
    )
)

DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8000"))
