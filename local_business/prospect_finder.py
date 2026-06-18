"""
Local-business prospect finder — the lead source that feeds the sales pipeline.

Where linkedin_pais.py hunts startup founders for internship networking, this
hunts the *customers* for the local-business product: dentists, gyms, salons,
taverns within a radius that show a VISIBLE revenue leak — the exact problem the
workflows plug. A visible leak is also the pitch, so the finding and the opener
are the same artifact.

Read-only: uses WebSearch/WebFetch via base.draft (the customer's own Claude
sub, no API key) to surface real, publicly-listed businesses + their leak
signal, then drops each into deals.py at the `prospect` stage. add_prospect()
dedups, so re-running is safe.

CLI:
    python -m local_business.prospect_finder "Royersford PA" --vertical dental --count 5
    python -m local_business.prospect_finder "Collegeville PA"        # mixed verticals
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .base import draft, parse_json
from .deals import Pipeline

log = logging.getLogger("prospect-finder")

# Rough monthly value a recovered leak is worth, by vertical — used only when the
# model can't estimate. Conservative on purpose; the pitch should under-promise.
DEFAULT_VALUE = {"hospitality": 300, "fitness": 400, "dental": 600,
                 "personal_care": 350, "default": 300}

# The leaks the product actually plugs — the model must tie each find to one.
LEAK_SIGNALS = (
    "no website or a dead/under-construction one; "
    "Google rating under 4.0 or fewer than ~40 reviews; "
    "recent reviews sitting unanswered; "
    "no online booking / 'call to book' only; "
    "listed hours look stale or inconsistent"
)

CONTEXT = """You are a lead researcher for a local-business AI service that plugs
revenue leaks (missed-call text-back, review responses, win-back, reminders).

Use WebSearch/WebFetch to find {count} REAL businesses near {location}{vertical_clause}
that are publicly listed (Google, Yelp, their own site) AND show at least one
visible leak: {signals}.

Rules:
  • Only include businesses you actually found in search results — never invent
    names, ratings, or contacts. If unsure, return fewer.
  • The leak must be specific and checkable (e.g. "3.6★, last 4 reviews unanswered",
    not "could improve online presence").
  • estimate `value` = realistic recovered dollars/MONTH if the leak were fixed.
  • `opener` = one warm, specific first-touch line referencing THEIR leak (no
    generic spam). This doubles as the deal's next_action.
  • contact_* only from public sources; use null if not publicly listed.

Output ONLY a JSON array:
[{{"name":"","vertical":"","city":"","leak":"<specific signal>","value":350,
   "opener":"<first-touch line>","contact_name":null,"contact_channel":null}}]
"""


async def find(location: str, *, vertical: str | None = None, count: int = 5) -> list[dict]:
    """Research prospects and add the new ones to the pipeline. Returns what was added."""
    prompt = CONTEXT.format(
        count=count, location=location, signals=LEAK_SIGNALS,
        vertical_clause=f" in the {vertical} vertical" if vertical else "",
    )
    raw = await draft(prompt, agent_name="prospect-finder")
    found = parse_json(raw)
    if not isinstance(found, list):
        log.warning("finder returned non-list; raw head: %s", raw[:200])
        return []

    pipeline = Pipeline.load_or_new()
    added: list[dict] = []
    for f in found:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        vert = f.get("vertical") or vertical or "default"
        value = int(f.get("value") or DEFAULT_VALUE.get(vert, DEFAULT_VALUE["default"]))
        note = _opener_note(f)
        did = pipeline.add_prospect(
            f["name"], vert, source="local-search",
            contact_name=f.get("contact_name"), channel=f.get("contact_channel"),
            value=value, note=note,
        )
        if did:                       # None means already in pipeline (deduped)
            added.append({"id": did, **f, "value": value})
    pipeline.save()
    return added


def _opener_note(f: dict) -> str:
    """Pack the leak + opener into the deal's next_action so it's actionable on sight."""
    leak = f.get("leak", "visible leak")
    opener = f.get("opener", "")
    return f"LEAK: {leak} · OPENER: {opener}" if opener else f"LEAK: {leak}"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Find local-business prospects with a visible leak")
    ap.add_argument("location", help='e.g. "Royersford PA"')
    ap.add_argument("--vertical", help="dental | fitness | hospitality | personal_care")
    ap.add_argument("--count", type=int, default=5)
    args = ap.parse_args()

    added = asyncio.run(find(args.location, vertical=args.vertical, count=args.count))
    if not added:
        print("No new prospects added (none found, or all already in pipeline).")
        return
    print(f"✓ added {len(added)} prospect(s) to the pipeline:\n")
    for a in added:
        print(f"  [{a['id']}] {a['name']} · {a.get('city','')} · ${a['value']:,}/mo")
        print(f"        {a.get('leak','')}")
        print(f"        opener: {a.get('opener','')}\n")
    print("Next: python -m local_business.deals      # see the board")


if __name__ == "__main__":
    main()
