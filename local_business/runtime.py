"""
pais-runtime — the single process that runs on the customer's machine.

Responsibilities:
  • Run the enabled agents (nightly batch + realtime missed-call events).
  • Heartbeat the PAIS website so the dashboard's "Agents online" pill is live.
  • Push the state blob the dashboard renders.
  • Pull owner approvals made in the dashboard and execute the sends.

Outbound HTTPS only — never opens an inbound port. Uses the customer's own
Claude subscription via tools.llm (no Anthropic API key).

Run modes:
    python -m local_business.runtime --business trappe-tavern --once
        run every enabled agent once, write .state/<id>.json (powers the demo)
    python -m local_business.runtime --business trappe-tavern --serve
        long-running: schedule + heartbeat + approval loop
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from . import business as biz
from .state import RuntimeState
from . import (reputation_workflow, reactivation_workflow, missedcall_workflow,
               reminders_workflow, digest_workflow)

log = logging.getLogger("pais-runtime")

PAIS_BASE = os.environ.get("PAIS_BASE", "https://pais-site.vercel.app")
HEARTBEAT_SECS = 300

AGENTS = {
    "reputation":   reputation_workflow,
    "reactivation": reactivation_workflow,
    "missedcall":   missedcall_workflow,
    "reminders":    reminders_workflow,
    "digest":       digest_workflow,
}
# Agents that run on the nightly batch (missedcall is realtime-only).
SCHEDULED = ["digest", "reputation", "reactivation", "reminders"]


async def run_batch(business, state) -> None:
    """Run each enabled scheduled agent once, in order."""
    for key in SCHEDULED:
        if not business.has_agent(key):
            continue
        mod = AGENTS[key]
        try:
            log.info("running %s for %s", key, business.business_id)
            await mod.run(business, state)
        except Exception as e:
            log.warning("%s failed: %s", key, e)
            state.add_activity(ic="!", ac="var(--red)", body=f"<b>{key}</b> hit an error: {e}")
    _recompute_leaks(state, business)
    state.save()


def _recompute_leaks(state, business) -> None:
    """Derive the dashboard's leak bars from current metrics."""
    m = state.data["metrics"]
    state.set_leaks([
        {"name": "Unanswered reviews → lost rank", "pct": min(95, m.get("reviews", 0) * 7),
         "val": f"{m.get('reviews', 0)} answered", "note": "+rank trend"},
        {"name": "After-hours calls → lost parties", "pct": min(90, m.get("calls", 0) * 8),
         "val": f"{m.get('calls', 0)} caught", "note": "high value"},
        {"name": "Customers drifting away", "pct": min(90, m.get("winbacks", 0) * 7),
         "val": f"{m.get('winbacks', 0)} re-engaged", "note": "recurring"},
        {"name": "No-shows on booked slots", "pct": min(80, m.get("noshows", 0) * 8),
         "val": f"{m.get('noshows', 0)} recovered", "note": "weekend"},
    ])


# ---- website sync (graceful no-op if offline / not yet built) ----
async def _post(path: str, payload: dict) -> dict | None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{PAIS_BASE}{path}", json=payload)
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        log.debug("sync %s skipped: %s", path, e)
        return None


async def heartbeat(business, state) -> None:
    await _post("/api/runtime/heartbeat", {
        "business_id": business.business_id,
        "agents": business.agents,
        "pending": state.pending_count,
    })


async def push_state(business, state) -> None:
    await _post("/api/runtime/state", {"business_id": business.business_id,
                                       "state": state.to_blob()})


async def pull_and_execute_approvals(business, state) -> None:
    """Owner decisions made in the dashboard → execute the sends here."""
    res = await _post("/api/runtime/approvals/pull", {"business_id": business.business_id})
    for decision in (res or {}).get("decisions", []):
        match = state.resolve_approval(decision["id"], sent=decision["action"] == "approve")
        if match and decision["action"] == "approve":
            # TODO: route to tools.sms / tools.reviews based on match["type"]/channel
            state.add_activity(ic="✓", ac="var(--green)", body=f"<b>Sent</b> — {match['who']}")
    state.save()


async def serve(business) -> None:
    state = RuntimeState.load_or_new(business.business_id)
    await run_batch(business, state)        # one batch on boot
    await push_state(business, state)
    while True:
        await heartbeat(business, state)
        await pull_and_execute_approvals(business, state)
        await push_state(business, state)
        await asyncio.sleep(HEARTBEAT_SECS)


async def once(business) -> None:
    state = RuntimeState.load_or_new(business.business_id)
    await run_batch(business, state)
    path = state.save()
    print(f"✓ ran {len([a for a in business.agents])} agents · {state.pending_count} drafts queued")
    print(f"  state → {path}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PAIS local-business runtime")
    ap.add_argument("--business", required=True, help="business id (config file stem)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="run agents once, write state.json")
    g.add_argument("--serve", action="store_true", help="long-running schedule + sync loop")
    args = ap.parse_args()

    business = biz.load(args.business)
    asyncio.run(once(business) if args.once else serve(business))


if __name__ == "__main__":
    main()
