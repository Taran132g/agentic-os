"""
Digest agent.

Nightly: roll up the day's POS data into a plain-English owner brief — busiest
hours, average ticket, top sellers, what to prep — plus the hour-by-hour bars
the dashboard draws.

Weakest standalone ROI, strongest stickiness: it's the screen the owner opens
every morning, so it keeps the whole product in front of them. Light until a
POS is connected (it estimates from booking/visit signals in the meantime).
"""

from __future__ import annotations

import logging

from .base import draft, parse_json, AGENT_META

log = logging.getLogger(__name__)
_running = False
KEY = "digest"


def is_running() -> bool:
    return _running


CONTEXT = """You are the Digest agent for {name}, a {vertical} business.
From the POS/booking signals provided, summarize yesterday. Output ONLY:
```json
{{
  "hours": [2,3,4,6,9,14,22,30,41,52,68,90,76,40],
  "peakIdx": 11,
  "avgTicket": 34,
  "peakHour": "11pm",
  "topItem": "Wings",
  "headline": "One sentence the owner reads first."
}}
```
`hours` = relative volume per open hour (first element = opening hour).
"""


async def run(business, state, *, broadcast=None, send_telegram=None, mode="scheduled"):
    global _running
    _running = True
    meta = AGENT_META[KEY]
    try:
        prompt = CONTEXT.format(name=business.name, vertical=business.vertical)
        raw = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
        out = parse_json(raw)
        if isinstance(out, list) and out:
            out = out[0]
        digest = out if isinstance(out, dict) else {}

        if digest:
            state.set_digest({
                "hours": digest.get("hours", []),
                "peakIdx": digest.get("peakIdx", 0),
                "avgTicket": digest.get("avgTicket", 0),
                "peakHour": digest.get("peakHour", "—"),
                "topItem": digest.get("topItem", "—"),
            })
        peak = digest.get("peakHour", "—")
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Digest", "on": True,
                              "last": digest.get("headline", "Compiled last night's room"),
                              "stat": str(peak), "lbl": "peak"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Digest</b> — {digest.get('headline', 'last night summarized')}")
        return 1
    finally:
        _running = False
