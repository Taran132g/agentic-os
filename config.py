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

# Base URL the PAIS dashboard is reachable at — used in Telegram notifications
# that link back to PAIS (e.g. the Content page). Override PAIS_URL in .env
# with a LAN IP or tunnel hostname to open those links from your phone.
PAIS_URL = os.environ.get("PAIS_URL", f"http://localhost:{DASHBOARD_PORT}").rstrip("/")
