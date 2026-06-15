"""
Reactivation (win-back) agent.

Weekly: scan booking/POS history for two cohorts —
  • regulars who've gone quiet (no visit in N+ days)
  • first-timers who never came back
— and draft a personal win-back offer for each cohort.

Clearest ROI story in the pitch: "we'll recover X lapsed customers worth $Y."
Best fit for gyms (churn) and dental/salon (recall).
"""

from __future__ import annotations

import logging

from .base import draft, parse_json, AGENT_META

log = logging.getLogger(__name__)
_running = False
KEY = "reactivation"

# A quiet regular is worth roughly this much in repeat visits if recovered.
DEFAULT_LTV = {"hospitality": 78, "fitness": 220, "personal_care": 140}


def is_running() -> bool:
    return _running


CONTEXT = """You are the Reactivation agent for {name}, a {vertical} business.
Brand voice: {voice}

From the booking/POS history provided (or gather what you can), identify:
  1. REGULARS who used to come often but haven't visited in {quiet_days}+ days.
  2. FIRST-TIMERS who came once and never returned.

Draft ONE win-back message per cohort — warm, short, with a low-friction offer
that fits a {vertical} business (free app / a free class / a hygiene-recall slot).
Never sound like spam. Output ONLY:
```json
{{
  "cohorts": [
    {{"who":"Win back <n> quiet regulars","count":7,"window":"40+ days","draft":"<msg>","unit_value":78}}
  ]
}}
```
"""


async def run(business, state, *, broadcast=None, send_telegram=None, mode="scheduled"):
    global _running
    _running = True
    meta = AGENT_META[KEY]
    quiet_days = business.extra.get("quiet_days", 40)
    try:
        prompt = CONTEXT.format(name=business.name, vertical=business.vertical,
                                voice=business.brand_voice, quiet_days=quiet_days)
        raw = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
        out = parse_json(raw) or {}
        cohorts = out.get("cohorts", []) if isinstance(out, dict) else []

        total = 0
        for c in cohorts:
            count = int(c.get("count", 0))
            unit = int(c.get("unit_value", DEFAULT_LTV.get(business.vertical, 80)))
            value = count * unit
            total += count
            state.queue_approval(
                agent=KEY, who=c.get("who", "Win back lapsed customers"),
                meta=f"no visit in {c.get('window','40+ days')}",
                context=f"<b>{count}</b> customers pulled from your history — quiet but reachable.",
                channel="Send via email" if business.channels.get("email") else "Owner-send SMS",
                draft=c.get("draft", ""), value=value,
            )
            state.bump_metric("winbacks", count)

        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Reactivation", "on": True,
                              "last": f"Found {total} customers to win back",
                              "stat": str(total), "lbl": "to win back"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Reactivation</b> found {total} lapsed customers")
        return total
    finally:
        _running = False
