"""
Reactivation (win-back) agent.

Weekly: scan booking/POS history for two cohorts —
  • regulars who've gone quiet (no visit in N+ days)
  • first-timers who never came back
— and draft a personal win-back offer for each cohort.

Clearest ROI story in the pitch: "we'll recover X lapsed customers worth $Y."
Best fit for gyms (churn) and dental/salon (recall).

Counts come from the client's real contact export (tools.contacts) whenever one
is connected, so the dashboard's "N to win back" is a hard number — the LLM only
writes the message, it never invents the count. If no source is wired yet, we
fall back to letting the model gather what it can and we flag the estimate.
"""

from __future__ import annotations

import logging

from .base import draft, parse_json, AGENT_META
from .tools import contacts

log = logging.getLogger(__name__)
_running = False
KEY = "reactivation"

# A recovered customer is worth roughly this much in repeat visits, by vertical.
DEFAULT_LTV = {"hospitality": 78, "fitness": 220, "personal_care": 140}


def is_running() -> bool:
    return _running


# ---- prompts ---------------------------------------------------------------
# Data path: we already know WHO and HOW MANY — the model only writes the note.
DRAFT_FROM_DATA = """You are the Reactivation agent for {name}, a {vertical} business.
Brand voice: {voice}

We pulled these customer cohorts from {name}'s own history. For EACH cohort,
write one warm, short win-back message (SMS-length) with a low-friction offer
that fits a {vertical} business. Use a real first name from the samples only as
a tone guide — the message will be personalized per recipient at send time, so
keep it naturally reusable. Never sound like spam.

Cohorts:
{cohorts}

Output ONLY this JSON, one drafts entry per cohort, same order:
```json
{{"drafts": ["<message for cohort 1>", "<message for cohort 2>"]}}
```
"""

# Fallback path: no contact source connected yet — the old gather-and-estimate.
GATHER_AND_ESTIMATE = """You are the Reactivation agent for {name}, a {vertical} business.
Brand voice: {voice}

No customer export is connected yet, so ESTIMATE conservatively from what's
typical for a {vertical} business this size. Identify:
  1. REGULARS who used to come often but haven't visited in {quiet_days}+ days.
  2. FIRST-TIMERS who came once and never returned.

Draft ONE win-back message per cohort — warm, short, low-friction offer. Output ONLY:
```json
{{"cohorts": [
  {{"who":"Win back <n> quiet regulars","count":7,"window":"{quiet_days}+ days","draft":"<msg>","unit_value":78}}
]}}
```
"""


def _cohorts_from_data(business, quiet_days: int) -> list[dict]:
    """Build cohorts with REAL counts from the connected contact source."""
    cohorts = []
    regulars = contacts.quiet_regulars(business, quiet_days=quiet_days)
    if regulars:
        cohorts.append({
            "who": f"Win back {len(regulars)} quiet regulars",
            "count": len(regulars),
            "window": f"{quiet_days}+ days",
            "samples": [c["name"] for c in regulars[:3] if c["name"]],
        })
    first_timers = contacts.first_timers_never_returned(business)
    if first_timers:
        cohorts.append({
            "who": f"Win back {len(first_timers)} first-timers who never returned",
            "count": len(first_timers),
            "window": "one visit, never came back",
            "samples": [c["name"] for c in first_timers[:3] if c["name"]],
        })
    return cohorts


async def _run_from_data(business, state, meta, cohorts, *, broadcast):
    """Real-data path: counts are hard, the model only writes the messages."""
    unit = DEFAULT_LTV.get(business.vertical, 80)
    listing = "\n".join(
        f"  {i+1}. {c['who']} ({c['window']}; e.g. {', '.join(c['samples']) or 'various'})"
        for i, c in enumerate(cohorts)
    )
    prompt = DRAFT_FROM_DATA.format(name=business.name, vertical=business.vertical,
                                    voice=business.brand_voice, cohorts=listing)
    raw = await draft(prompt, agent_name=f"{business.business_id}:{KEY}", broadcast=broadcast)
    out = parse_json(raw) or {}
    drafts = out.get("drafts", []) if isinstance(out, dict) else []

    total = 0
    for i, c in enumerate(cohorts):
        count = c["count"]
        value = count * unit
        total += count
        msg = drafts[i] if i < len(drafts) else ""
        state.queue_approval(
            agent=KEY, who=c["who"], meta=c["window"],
            context=f"<b>{count}</b> customers pulled from your history — quiet but reachable.",
            channel="Send via email" if business.channels.get("email") else "Owner-send SMS",
            draft=msg, value=value,
        )
        state.bump_metric("winbacks", count)
    return total


async def _run_estimated(business, state, meta, quiet_days, *, broadcast):
    """Fallback path: no source connected — the model estimates, flagged as est."""
    prompt = GATHER_AND_ESTIMATE.format(name=business.name, vertical=business.vertical,
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
            meta=f"{c.get('window', f'{quiet_days}+ days')} · est.",
            context=f"<b>~{count}</b> customers (estimated — connect your customer "
                    f"list for exact names).",
            channel="Send via email" if business.channels.get("email") else "Owner-send SMS",
            draft=c.get("draft", ""), value=value,
        )
        state.bump_metric("winbacks", count)
    return total


async def run(business, state, *, broadcast=None, send_telegram=None, mode="scheduled"):
    global _running
    _running = True
    meta = AGENT_META[KEY]
    quiet_days = business.extra.get("quiet_days", 40)
    connected = contacts.is_connected(business)
    try:
        if connected:
            cohorts = _cohorts_from_data(business, quiet_days)
            total = await _run_from_data(business, state, meta, cohorts, broadcast=broadcast) \
                if cohorts else 0
        else:
            total = await _run_estimated(business, state, meta, quiet_days, broadcast=broadcast)

        source = "your customer list" if connected else "estimate (no list connected)"
        state.set_agent_card({"key": KEY, "ic": meta["ic"], "ac": meta["ac"],
                              "name": "Reactivation", "on": True,
                              "last": f"Found {total} customers to win back · {source}",
                              "stat": str(total), "lbl": "to win back"})
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Reactivation</b> found {total} lapsed customers "
                                f"({'from your list' if connected else 'estimated'})")
        return total
    finally:
        _running = False
