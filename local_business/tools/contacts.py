"""
Contacts / history tool — the customer list the win-back + reminder agents read.

Source depends on what the business already runs:
  • POS export (Toast/Square) — richest: visit dates, ticket size, items.
  • Booking system (Mindbody, dental PMS) — appointment + recall history.
  • Reservation phone log — the fallback for walk-in spots like Trappe Tavern
    that have no POS: names + numbers gathered when they call to book.

Returns plain dicts so the agents stay storage-agnostic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def quiet_regulars(business, *, quiet_days: int = 40) -> list[dict]:
    """Customers who used to come often but haven't recently. TODO: real source."""
    src = business.integrations.get("pos") or business.integrations.get("booking")
    if not src:
        return []  # phone-log fallback handled inside the agent prompt for now
    raise NotImplementedError(f"connect {src} export to list quiet regulars")


def first_timers_never_returned(business) -> list[dict]:
    """One visit, never came back. TODO: real source."""
    return []


def tomorrows_bookings(business) -> list[dict]:
    """Tomorrow's appointment/reservation list for the reminders agent."""
    if not business.integrations.get("booking"):
        return []
    raise NotImplementedError("connect booking system to read tomorrow's list")


def recent_happy_visitors(business) -> list[dict]:
    """Guests with a standout recent visit — review-ask candidates. TODO."""
    return []
