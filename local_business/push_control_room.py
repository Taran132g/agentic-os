"""
Push real Control Room data → the agenticos web app (pais-control.vercel.app).

Phase 2 of the standalone Control Room: instead of the seeded demo, the signed-in
web app renders whatever sits in the owner's Supabase `user_metadata.cr_state`.
This script builds that blob from the business's REAL data (the contact CSVs read
by tools/contacts.py — quiet regulars, lapsed first-timers, tomorrow's bookings)
and writes it to the target user via the Supabase Auth admin API (service-role
key). No table, no backend endpoint — the app reads it straight off the session.

    python -m local_business.push_control_room --business copper-lantern \
        --email taran.impact@gmail.com [--dry-run]

Service-role key + URL are read from ~/FindingFounders/backend/.env.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from . import business as biz
from .reactivation_workflow import DEFAULT_LTV
from .tools import contacts

ENV_PATH = Path.home() / "FindingFounders" / "backend" / ".env"


def _load_env() -> tuple[str, str]:
    url = key = None
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_URL="):
            url = line.split("=", 1)[1].strip()
        elif line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
            key = line.split("=", 1)[1].strip()
    if not url or not key:
        sys.exit(f"Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY in {ENV_PATH}")
    return url, key


def _first(name: str) -> str:
    return (name or "").split()[0] if name else "there"


def build_payload(business) -> dict:
    """The cr_state blob the web app renders — built from REAL contact data."""
    quiet_days = business.extra.get("quiet_days", 40)
    unit = DEFAULT_LTV.get(business.vertical, 80)
    name = business.name
    regs = contacts.quiet_regulars(business, quiet_days=quiet_days)
    firsts = contacts.first_timers_never_returned(business)
    books = contacts.tomorrows_bookings(business)

    approvals: list[dict] = []
    if regs:
        names = ", ".join(_first(c["name"]) for c in regs[:4] if c["name"])
        approvals.append({
            "id": "winback-regulars", "ic": "↩", "cls": "",
            "who": f"Win back {len(regs)} quiet regulars",
            "meta": f"no visit in {quiet_days}+ days · from your list",
            "val": len(regs) * unit,
            "ctx": f"<b>{len(regs)}</b> regulars pulled from your customer list — quiet but reachable. {names}.",
            "draft": f"\"Hey {_first(regs[0]['name'])} — we've been missing you at {name}! "
                     f"Swing by this week and something's on us.\"",
            "channel": "Owner-send SMS" if business.channels.get("sms") else "Send via email",
        })
    if firsts:
        approvals.append({
            "id": "winback-firsttimers", "ic": "↩", "cls": "",
            "who": f"Win back {len(firsts)} first-timers who never returned",
            "meta": "one visit, never came back",
            "val": len(firsts) * unit,
            "ctx": f"<b>{len(firsts)}</b> first-timers from your list — came once, worth a second look.",
            "draft": f"\"Hi! We loved having you at {name}. Come back this month and the first one's "
                     f"on us — we'd love a round two.\"",
            "channel": "Send via email" if business.channels.get("email") else "Owner-send SMS",
        })
    if books:
        who = " · ".join(_first(b["name"]) for b in books[:3] if b["name"])
        b0 = books[0]
        approvals.append({
            "id": "reminders", "ic": "⏰", "cls": "",
            "who": f"{len(books)} reminders for tomorrow's book",
            "meta": who,
            "val": 0,
            "ctx": "Confirm tomorrow's reservations before the day starts.",
            "draft": f"\"Hi {_first(b0['name'])}! Confirming your table for {b0.get('party_size','?')} "
                     f"tomorrow at {b0.get('time','')}. Reply YES and you're all set.\"",
            "channel": "Owner-send SMS",
        })

    return {"recovered": 0, "approvals": approvals, "resolved": []}


# ---- Supabase Auth admin (service-role) ------------------------------------
def _find_user(url: str, key: str, email: str) -> dict | None:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{url}/auth/v1/admin/users", headers=h,
                     params={"page": 1, "per_page": 200}, timeout=20)
    r.raise_for_status()
    users = r.json().get("users", r.json() if isinstance(r.json(), list) else [])
    return next((u for u in users if (u.get("email") or "").lower() == email.lower()), None)


def _push(url: str, key: str, user: dict, cr_state: dict) -> None:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    merged = dict(user.get("user_metadata") or {})
    merged["cr_state"] = cr_state
    r = requests.put(f"{url}/auth/v1/admin/users/{user['id']}", headers=h,
                     json={"user_metadata": merged}, timeout=20)
    r.raise_for_status()


def main() -> None:
    ap = argparse.ArgumentParser(description="Push real Control Room data to a user's Supabase profile")
    ap.add_argument("--business", default="copper-lantern")
    ap.add_argument("--email", required=True, help="owner's Google/Supabase email")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    business = biz.load(args.business)
    if not contacts.is_connected(business):
        sys.exit(f"No contact source for {business.business_id} — add data/{business.business_id}/contacts.csv")

    payload = build_payload(business)
    print(f"\n  cr_state for {business.name} → {args.email}")
    print(f"  {len(payload['approvals'])} approvals · ${sum(a['val'] for a in payload['approvals']):,} of pipeline\n")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.dry_run:
        print("\n  (dry run — nothing pushed)")
        return

    url, key = _load_env()
    user = _find_user(url, key, args.email)
    if not user:
        sys.exit(f"\n  No Supabase user with email {args.email} — sign in to the app once first.")
    _push(url, key, user, payload)
    print(f"\n  ✓ pushed to {args.email} (user {user['id'][:8]}…). Sign in to see it live.")


if __name__ == "__main__":
    main()
