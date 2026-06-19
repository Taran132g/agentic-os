"""
Contacts / history tool — the customer list the win-back + reminder agents read.

This is the data layer a CRM lives or dies on. Until a client's POS/booking
system has a live API adapter, the universal lowest-common-denominator source is
a **CSV export** — every platform a local business already uses (Square, Toast,
Mindbody, most dental PMSs, even a reservation phone log kept in a spreadsheet)
can produce one. So onboarding a new client is: drop their export at

    local_business/data/<business_id>/contacts.csv
    local_business/data/<business_id>/bookings.csv   (optional, for reminders)

and these readers turn it into the cohorts the agents act on. Swap a real API
adapter in later behind the same function signatures and nothing downstream
changes.

contacts.csv columns (extra columns are ignored; missing ones degrade gracefully):
    name, phone, email, first_visit, last_visit, visit_count, avg_ticket

bookings.csv columns:
    name, phone, date, time, party_size, notes

Everything returns plain dicts so the agents stay storage-agnostic.

CLI — see exactly what the agents would see for a business:
    python -m local_business.tools.contacts copper-lantern
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# A "regular" is someone with at least this many recorded visits.
REGULAR_MIN_VISITS = 3
# A first-timer counts as "never returned" once this long has passed.
FIRST_TIMER_LAPSE_DAYS = 30
# A visit this recent makes someone a review-ask candidate.
HAPPY_RECENT_DAYS = 3


# ---- file resolution -------------------------------------------------------
def _path(business, filename: str) -> Path:
    """Per-business data file, with an optional explicit override in the config.

    `integrations.contacts_csv` / `integrations.bookings_csv` win if set; else we
    fall back to the conventional data/<business_id>/<filename> location.
    """
    override = business.integrations.get(filename.replace(".csv", "_csv"))
    if override:
        return Path(override).expanduser()
    return DATA_DIR / business.business_id / filename


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f)]


# ---- small coercions (CSV exports are messy) -------------------------------
def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _customer(row: dict) -> dict:
    """Normalize one CSV row into the dict shape the agents consume."""
    last = _parse_date(row.get("last_visit"))
    return {
        "name": (row.get("name") or "").strip(),
        "phone": (row.get("phone") or "").strip(),
        "email": (row.get("email") or "").strip(),
        "first_visit": _parse_date(row.get("first_visit")),
        "last_visit": last,
        "visit_count": _to_int(row.get("visit_count"), default=1),
        "avg_ticket": _to_int(row.get("avg_ticket"), default=0),
        "days_since_visit": (date.today() - last).days if last else None,
    }


def _load_customers(business) -> list[dict]:
    return [_customer(r) for r in _read_csv(_path(business, "contacts.csv"))]


def is_connected(business) -> bool:
    """True when this business actually has a contact source wired up."""
    return _path(business, "contacts.csv").exists()


# ---- the four reads the agents call ----------------------------------------
def quiet_regulars(business, *, quiet_days: int = 40) -> list[dict]:
    """Customers who used to come often but haven't recently."""
    return [
        c for c in _load_customers(business)
        if c["visit_count"] >= REGULAR_MIN_VISITS
        and c["days_since_visit"] is not None
        and c["days_since_visit"] >= quiet_days
    ]


def first_timers_never_returned(business) -> list[dict]:
    """One visit, never came back (and enough time has passed to be sure)."""
    return [
        c for c in _load_customers(business)
        if c["visit_count"] <= 1
        and c["days_since_visit"] is not None
        and c["days_since_visit"] >= FIRST_TIMER_LAPSE_DAYS
    ]


def recent_happy_visitors(business) -> list[dict]:
    """Guests with a standout recent visit — review-ask candidates."""
    return [
        c for c in _load_customers(business)
        if c["days_since_visit"] is not None
        and c["days_since_visit"] <= HAPPY_RECENT_DAYS
    ]


def tomorrows_bookings(business) -> list[dict]:
    """Tomorrow's appointment/reservation list for the reminders agent."""
    target = date.today() + timedelta(days=1)
    out = []
    for row in _read_csv(_path(business, "bookings.csv")):
        if _parse_date(row.get("date")) == target:
            out.append({
                "name": (row.get("name") or "").strip(),
                "phone": (row.get("phone") or "").strip(),
                "time": (row.get("time") or "").strip(),
                "party_size": _to_int(row.get("party_size"), default=0),
                "notes": (row.get("notes") or "").strip(),
            })
    return out


# ---- CLI -------------------------------------------------------------------
def _fmt(customers: list[dict], *, limit: int = 4) -> str:
    if not customers:
        return "    (none)"
    lines = []
    for c in customers[:limit]:
        d = c.get("days_since_visit")
        ago = f"{d}d ago" if d is not None else "—"
        lines.append(f"    · {c['name'] or '—':<18} {c.get('phone',''):<16} "
                     f"{c.get('visit_count','?')} visits · last {ago}")
    if len(customers) > limit:
        lines.append(f"    … +{len(customers) - limit} more")
    return "\n".join(lines)


def main() -> None:
    import sys
    from .. import business as biz  # type: ignore

    if len(sys.argv) < 2:
        sys.exit("usage: python -m local_business.tools.contacts <business_id>")
    business = biz.load(sys.argv[1])

    if not is_connected(business):
        print(f"\n  {business.name}: no contact source connected "
              f"(expected {_path(business, 'contacts.csv')}).\n")
        return

    quiet_days = business.extra.get("quiet_days", 40)
    qr = quiet_regulars(business, quiet_days=quiet_days)
    ft = first_timers_never_returned(business)
    hv = recent_happy_visitors(business)
    tb = tomorrows_bookings(business)

    print(f"\n  CONTACTS · {business.name} ({business.vertical})")
    print(f"  source: {_path(business, 'contacts.csv')}\n")
    print(f"  Quiet regulars (no visit in {quiet_days}+ days):  {len(qr)}")
    print(_fmt(qr))
    print(f"\n  First-timers who never returned:              {len(ft)}")
    print(_fmt(ft))
    print(f"\n  Recent happy visitors (review candidates):    {len(hv)}")
    print(_fmt(hv))
    print(f"\n  Bookings tomorrow (reminders agent):          {len(tb)}")
    if tb:
        for b in tb:
            print(f"    · {b['name'] or '—':<18} {b['time']:<7} party {b['party_size']}"
                  f"{' · ' + b['notes'] if b['notes'] else ''}")
    print()


if __name__ == "__main__":
    main()
