"""
Runtime state — the single blob the on-machine runtime pushes to the PAIS
website and that the customer dashboard renders.

Persisted as JSON next to the runtime (swap for SQLite when a client needs
history/analytics). Mirrors the shape app.js consumes, so the dashboard can
read a real run with zero changes:

    { recovered, metrics, approvals[], agents[], leaks[], digest, feed[] }

State mutation is funnelled through small methods so agents never touch the
dict directly — keeps the write path auditable.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(__file__).parent / ".state"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


class RuntimeState:
    def __init__(self, business_id: str):
        self.business_id = business_id
        self.path = STATE_DIR / f"{business_id}.json"
        self.data = {
            "business_id": business_id,
            "updated_at": _now_iso(),
            "recovered": 0,
            "metrics": {"reviews": 0, "winbacks": 0, "calls": 0, "noshows": 0},
            "approvals": [],
            "agents": [],
            "leaks": [],
            "digest": {},
            "feed": [],
        }

    # ---- load / save ----
    @classmethod
    def load_or_new(cls, business_id: str) -> "RuntimeState":
        s = cls(business_id)
        if s.path.exists():
            s.data = json.loads(s.path.read_text(encoding="utf-8"))
        return s

    def save(self) -> Path:
        self.data["updated_at"] = _now_iso()
        STATE_DIR.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        return self.path

    # ---- mutators (the only sanctioned write path) ----
    def queue_approval(self, *, agent: str, who: str, meta: str, context: str,
                       channel: str, draft: str, value: int = 0) -> str:
        aid = _new_id()
        self.data["approvals"].append({
            "id": aid, "type": agent, "who": who, "meta": meta,
            "context": context, "channel": channel, "draft": draft,
            "value": value, "queued_at": _now_iso(),
        })
        return aid

    def resolve_approval(self, approval_id: str, *, sent: bool) -> dict | None:
        match = next((a for a in self.data["approvals"] if a["id"] == approval_id), None)
        if not match:
            return None
        self.data["approvals"] = [a for a in self.data["approvals"] if a["id"] != approval_id]
        if sent and match.get("value"):
            self.data["recovered"] += int(match["value"])
        return match

    def bump_metric(self, key: str, by: int = 1) -> None:
        self.data["metrics"][key] = self.data["metrics"].get(key, 0) + by

    def add_activity(self, *, ic: str, ac: str, body: str) -> None:
        self.data["feed"].insert(0, {"ic": ic, "ac": ac, "body": body, "time": "just now"})
        self.data["feed"] = self.data["feed"][:40]

    def set_agent_card(self, card: dict) -> None:
        others = [a for a in self.data["agents"] if a["key"] != card["key"]]
        self.data["agents"] = others + [card]

    def set_leaks(self, leaks: list) -> None:
        self.data["leaks"] = leaks

    def set_digest(self, digest: dict) -> None:
        self.data["digest"] = digest

    @property
    def pending_count(self) -> int:
        return len(self.data["approvals"])

    def to_blob(self) -> dict:
        return self.data
