"""
Reminders agent.

Nightly (T-1 day): work tomorrow's appointment/reservation book — draft a
confirmation for each booking, a gentle nudge for unconfirmed ones, and pull a
recall list of long-overdue guests.

High value ONLY for appointment-driven verticals (dental, salon, fitness
classes, sit-down reservations). Idle for walk-in spots until a booking system
is connected — the agent self-disables when integrations.booking is null.
"""

from __future__ import annotations

import logging

from .base import draft, parse_json, AGENT_META

log = logging.getLogger(__name__)
_running = False
KEY = "reminders"

NOSHOW_VALUE = {"hospitality": 75, "fitness": 25, "personal_care": 110}


def is_running() -> bool:
    return _running


CONTEXT = """You are the Reminders agent for {name}, a {vertical} business.
Brand voice: {voice}

From tomorrow's booking list, draft:
  1. A short confirmation message per appointment.
  2. A warmer nudge for anyone who hasn't confirmed.
Then list overdue guests due for a recall.
Output ONLY:
```json
{{
  "confirms": [{{"who":"Confirm <n> bookings for tomorrow","count":12,"draft":"<msg>","value":0}}],
  "nudges":   [{{"who":"Nudge <n> unconfirmed","count":3,"draft":"<msg>","value":225}}]
}}
```
"""


async def run(business, state, *, broadcast=None, send_telegram=None, mode="scheduled"):
    global _running
    _running = True
    meta = AGENT_META[KEY]

    # Self-disable when there's no booking system to read.
    if not business.integrations.get("booking"):
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": "var(--text-faint)",
                              "name": "Reminders", "on": False,
                              "last": "Idle — needs a booking system", "stat": "—", "lbl": "off"})
        _running = False
        return 0

    try:
        prompt = CONTEXT.format(name=business.name, vertical=business.vertical,
                                voice=business.brand_voice)
        raw = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
        out = parse_json(raw) or {}
        items = (out.get("confirms", []) + out.get("nudges", [])) if isinstance(out, dict) else []

        saved = 0
        for it in items:
            value = int(it.get("value", 0))
            if value:
                saved += int(it.get("count", 1))
            state.queue_approval(
                agent=KEY, who=it.get("who", "Appointment reminders"),
                meta="tomorrow's book", context="Pulled from your booking system for tomorrow.",
                channel="Owner-send SMS" if business.sms_is_owner_send else "Send via email",
                draft=it.get("draft", ""), value=value,
            )
        if saved:
            state.bump_metric("noshows", saved)
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Reminders", "on": True,
                              "last": f"Worked tomorrow's book ({len(items)} drafts)",
                              "stat": str(state.data["metrics"].get("noshows", 0)), "lbl": "no-shows saved"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Reminders</b> prepped tomorrow's book for {business.name}")
        return len(items)
    finally:
        _running = False
