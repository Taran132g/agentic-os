"""
POS tool — daily rollups for the Digest agent.

Adapters per platform; we confirmed in the outreach research that
Collegeville Italian Bakery is on Toast and Sweet Brew is on Square, while
Trappe Tavern has no POS integration (digest stays estimate-only there).
"""

from __future__ import annotations


def daily_rollup(business) -> dict:
    """Yesterday's hours/ticket/items. TODO: Toast + Square adapters."""
    platform = business.integrations.get("pos")
    if platform == "toast":
        raise NotImplementedError("Toast Orders API adapter")
    if platform == "square":
        raise NotImplementedError("Square Orders API adapter")
    # No POS: the Digest agent estimates from booking/visit signals instead.
    return {}
