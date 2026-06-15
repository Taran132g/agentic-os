"""
Per-business config loader.

Each client is one JSON file under local_business/businesses/<id>.json.
The runtime loads it once at startup and hands the frozen Business object to
every agent so they share the same voice, channels, and enabled-agent list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

BUSINESSES_DIR = Path(__file__).parent / "businesses"


@dataclass(frozen=True)
class Business:
    business_id: str
    name: str
    vertical: str                      # hospitality | fitness | personal_care
    brand_voice: str
    channels: dict                     # {"email": True, "sms": "owner-send", "telegram_chat_id": "..."}
    integrations: dict                 # {"reviews": "google", "pos": None, "booking": "phone"}
    agents: list                       # ["reputation", "reactivation", ...]
    extra: dict = field(default_factory=dict)

    @property
    def can_send_sms(self) -> bool:
        return bool(self.channels.get("sms"))

    @property
    def sms_is_owner_send(self) -> bool:
        # Owner is the sender → no A2P 10DLC / TCPA registration burden on us.
        return self.channels.get("sms") == "owner-send"

    def has_agent(self, key: str) -> bool:
        return key in self.agents


def load(business_id: str) -> Business:
    path = BUSINESSES_DIR / f"{business_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No business config at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))

    known = {"business_id", "name", "vertical", "brand_voice",
             "channels", "integrations", "agents"}
    extra = {k: v for k, v in raw.items() if k not in known}
    return Business(
        business_id=raw["business_id"],
        name=raw["name"],
        vertical=raw["vertical"],
        brand_voice=raw["brand_voice"],
        channels=raw.get("channels", {}),
        integrations=raw.get("integrations", {}),
        agents=raw.get("agents", []),
        extra=extra,
    )


def load_all() -> list[Business]:
    return [load(p.stem) for p in sorted(BUSINESSES_DIR.glob("*.json"))]
