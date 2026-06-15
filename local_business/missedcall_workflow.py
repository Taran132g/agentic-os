"""
Missed-Call agent.

Real-time: a missed-call webhook fires → draft an instant text-back so an
after-hours or too-busy-to-answer lead doesn't just call the next place.

Highest raw ROI of the fleet (missed call = lost booking/party), and the most
visceral pitch line: "how many calls did you miss last month?"

Realtime trigger, not a nightly batch — `run()` handles one call event.
"""

from __future__ import annotations

import logging

from .base import draft, AGENT_META

log = logging.getLogger(__name__)
_running = False
KEY = "missedcall"

# Estimated value of a recovered inbound call by vertical (a party, a booking…).
CALL_VALUE = {"hospitality": 180, "fitness": 90, "personal_care": 120}


def is_running() -> bool:
    return _running


CONTEXT = """You are the Missed-Call agent for {name}, a {vertical} business.
Brand voice: {voice}

A call came in at {when} and rang out ({reason}). Draft ONE short, friendly SMS
the owner can send back from their own number. Acknowledge the miss, ask what
they needed (booking / party / question), and invite a reply. Keep it under 320
characters, no links unless natural. Output ONLY the message text, nothing else.
"""


async def run(business, state, *, call=None, broadcast=None, send_telegram=None, mode="realtime"):
    """`call` = {"number": "...", "when": "9:52pm", "reason": "after the host left"}."""
    global _running
    _running = True
    meta = AGENT_META[KEY]
    call = call or {"number": "unknown", "when": "just now", "reason": "the floor was slammed"}
    try:
        prompt = CONTEXT.format(name=business.name, vertical=business.vertical,
                                voice=business.brand_voice,
                                when=call["when"], reason=call["reason"])
        msg = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
        value = CALL_VALUE.get(business.vertical, 120)

        state.queue_approval(
            agent=KEY, who=f"Text back {call['number']}",
            meta=f"missed at {call['when']}",
            context=f"<b>Live:</b> call rang out — {call['reason']}.",
            channel="Owner-send SMS", draft=msg, value=value,
        )
        state.bump_metric("calls")
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Missed-Call", "on": True,
                              "last": f"Caught a call at {call['when']}",
                              "stat": str(state.data["metrics"].get("calls", 0)), "lbl": "saved / mo"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Missed-Call</b> caught {call['number']} — text-back ready")
        return 1
    finally:
        _running = False
