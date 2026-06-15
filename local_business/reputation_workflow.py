"""
Reputation agent.

Two jobs every night:
  1. Find new Google reviews → draft owner-voiced replies (esp. the angry ones).
  2. Spot last night's happy guests → draft a 5★ review ask.

Plugs the biggest universal leak: unanswered reviews + thin review volume drag
down "near me" rank for all 12 outreach businesses.
"""

from __future__ import annotations

import logging

from .base import draft, parse_json, request_send, AGENT_META

log = logging.getLogger(__name__)
_running = False
KEY = "reputation"


def is_running() -> bool:
    return _running


CONTEXT = """You are the Reputation agent for {name}, a {vertical} business.
Brand voice: {voice}

You have two tasks. Use Bash/WebSearch/WebFetch to gather, then OUTPUT ONLY JSON.

TASK 1 — New reviews to answer:
Find Google reviews posted since the last cursor. For each, draft a reply in the
owner's voice. Angry/low-star reviews get a warm, specific, non-defensive reply
that names the fix. Happy reviews get a short genuine thank-you.

TASK 2 — Review asks:
From the visit/POS signals provided, pick guests who clearly had a great visit
and draft a short, personal ask for a Google review (include [review link]).

Output ONLY:
```json
{{
  "replies":  [{{"who":"Reply to <name>","stars":2,"meta":"★★☆☆☆ · 2h ago","review":"<their text>","draft":"<reply>"}}],
  "asks":     [{{"who":"Ask <n> happy guests for a review","count":6,"draft":"<ask msg>","value":220}}]
}}
```
"""


async def run(business, state, *, broadcast=None, send_telegram=None, mode="scheduled"):
    global _running
    _running = True
    meta = AGENT_META[KEY]
    try:
        prompt = CONTEXT.format(name=business.name, vertical=business.vertical,
                                voice=business.brand_voice)
        # In production the runtime appends real signals here (review API cursor,
        # last-night POS rows). For now the prompt instructs Claude to gather.
        raw = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
        out = parse_json(raw) or {}
        replies = out.get("replies", []) if isinstance(out, dict) else []
        asks = out.get("asks", []) if isinstance(out, dict) else []

        for r in replies:
            state.queue_approval(
                agent=KEY, who=r.get("who", "Reply to a review"),
                meta=r.get("meta", "review"), context=f"Review: “{r.get('review','')}”",
                channel="Post as Google reply", draft=r.get("draft", ""), value=0,
            )
            state.bump_metric("reviews")

        for a in asks:
            state.queue_approval(
                agent=KEY, who=a.get("who", "Ask happy guests for a review"),
                meta="5★ visits · last night",
                context=f"<b>{a.get('count','several')}</b> guests had a standout visit — prime 5★ candidates.",
                channel="Send via email" if business.channels.get("email") else "Owner-send SMS",
                draft=a.get("draft", ""), value=int(a.get("value", 0)),
            )

        n = len(replies) + len(asks)
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Reputation", "on": True,
                              "last": f"Drafted {len(replies)} replies + {len(asks)} asks",
                              "stat": str(state.data["metrics"].get("reviews", 0)), "lbl": "★ handled"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Reputation</b> queued {n} drafts for {business.name}")
        return n
    finally:
        _running = False
