"""
Sales pipeline — the CRM layer PAIS was missing.

`RuntimeState` tracks a business you've already *onboarded*. This tracks the
ones you're still trying to *close*: every prospect, what stage of the sale
they're at, and a timestamped history of each touch.

It's the cheap, single-builder replacement for GoHighLevel/HubSpot — one JSON
file behind funneled mutators, the same shape as state.py, so lead sources
(linkedin_pais, a future Maps scraper) get a destination instead of stdout:

    enrich a lead → pipeline.add_prospect(...) → advance() as the deal moves.

Stages model the real motion:

    prospect → contacted → demo_sent → call_booked → closed_won
                                                   ↘ closed_lost

CLI:
    python -m local_business.deals                       # show the board
    python -m local_business.deals add "Trappe Tavern" hospitality linkedin
    python -m local_business.deals advance <id> demo_sent "sent loom + leak sheet"
    python -m local_business.deals stats
"""

from __future__ import annotations

import sys
from pathlib import Path

from .state import STATE_DIR, _now_iso, _new_id  # reuse the same conventions

PIPELINE_PATH = STATE_DIR / "pipeline.json"

# Ordered stages. Everything before the two terminal stages is an "open" deal.
STAGES = ["prospect", "contacted", "demo_sent", "call_booked",
          "closed_won", "closed_lost"]
TERMINAL = {"closed_won", "closed_lost"}
OPEN_STAGES = [s for s in STAGES if s not in TERMINAL]


def _norm(name: str) -> str:
    """Normalize a business name for dedup (case/space-insensitive)."""
    return " ".join(name.lower().split())


class Pipeline:
    def __init__(self) -> None:
        self.path = PIPELINE_PATH
        self.deals: list[dict] = []

    # ---- load / save (mirrors RuntimeState) ----
    @classmethod
    def load_or_new(cls) -> "Pipeline":
        p = cls()
        if p.path.exists():
            import json
            p.deals = json.loads(p.path.read_text(encoding="utf-8"))
        return p

    def save(self) -> Path:
        import json
        STATE_DIR.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.deals, indent=2), encoding="utf-8")
        return self.path

    # ---- mutators (the only sanctioned write path) ----
    def add_prospect(self, name: str, vertical: str, *, source: str = "manual",
                     contact_name: str | None = None, contact_role: str | None = None,
                     channel: str | None = None, value: int = 0,
                     note: str | None = None) -> str | None:
        """Add a new prospect. Returns its id, or None if already in the pipeline."""
        if self.find_by_name(name):
            return None
        did = _new_id()
        now = _now_iso()
        self.deals.append({
            "id": did,
            "name": name,
            "vertical": vertical,
            "source": source,
            "contact": {"name": contact_name, "role": contact_role, "channel": channel},
            "stage": "prospect",
            "value": int(value),          # estimated monthly/recovered value
            "next_action": note or "Send first touch",
            "created_at": now,
            "updated_at": now,
            "history": [{"at": now, "stage": "prospect",
                         "note": note or "Added from " + source}],
        })
        return did

    def advance(self, deal_id: str, to_stage: str, *, note: str | None = None) -> dict | None:
        """Move a deal to a new stage and record the transition in its history."""
        if to_stage not in STAGES:
            raise ValueError(f"unknown stage {to_stage!r} — pick from {STAGES}")
        deal = self.get(deal_id)
        if not deal:
            return None
        now = _now_iso()
        deal["stage"] = to_stage
        deal["updated_at"] = now
        deal["history"].append({"at": now, "stage": to_stage,
                                "note": note or f"→ {to_stage}"})
        if to_stage in TERMINAL:
            deal["next_action"] = "—"
        return deal

    def log_touch(self, deal_id: str, note: str, *, channel: str | None = None) -> dict | None:
        """Record a touch (call, email, follow-up) without changing the stage."""
        deal = self.get(deal_id)
        if not deal:
            return None
        now = _now_iso()
        deal["updated_at"] = now
        deal["history"].append({"at": now, "stage": deal["stage"],
                                "note": note, "channel": channel})
        return deal

    def set_next_action(self, deal_id: str, action: str) -> dict | None:
        deal = self.get(deal_id)
        if deal:
            deal["next_action"] = action
            deal["updated_at"] = _now_iso()
        return deal

    # ---- reads ----
    def get(self, deal_id: str) -> dict | None:
        return next((d for d in self.deals if d["id"] == deal_id), None)

    def find_by_name(self, name: str) -> dict | None:
        key = _norm(name)
        return next((d for d in self.deals if _norm(d["name"]) == key), None)

    def by_stage(self, stage: str) -> list[dict]:
        return [d for d in self.deals if d["stage"] == stage]

    def open_deals(self) -> list[dict]:
        return [d for d in self.deals if d["stage"] not in TERMINAL]

    def stats(self) -> dict:
        """Stage counts, win rate, and open pipeline value — the numbers to optimize."""
        counts = {s: len(self.by_stage(s)) for s in STAGES}
        won = counts["closed_won"]
        closed = won + counts["closed_lost"]
        return {
            "total": len(self.deals),
            "counts": counts,
            "open_value": sum(d["value"] for d in self.open_deals()),
            "won_value": sum(d["value"] for d in self.by_stage("closed_won")),
            "win_rate": round(won / closed, 2) if closed else None,
        }


# ---- CLI -------------------------------------------------------------------
def _print_board(p: "Pipeline") -> None:
    s = p.stats()
    print(f"\n  PIPELINE · {s['total']} deals · "
          f"${s['open_value']:,} open · ${s['won_value']:,} won · "
          f"win rate {int(s['win_rate']*100) if s['win_rate'] is not None else '—'}"
          f"{'%' if s['win_rate'] is not None else ''}\n")
    for stage in STAGES:
        deals = p.by_stage(stage)
        if not deals:
            continue
        print(f"  {stage.upper()}  ({len(deals)})")
        for d in deals:
            who = d["contact"]["name"] or "—"
            print(f"    [{d['id']}] {d['name']} · {d['vertical']} · {who}"
                  f"{f' · ${d['value']:,}' if d['value'] else ''}")
            print(f"           next: {d['next_action']}")
        print()


def main() -> None:
    p = Pipeline.load_or_new()
    args = sys.argv[1:]
    cmd = args[0] if args else "list"

    if cmd == "list":
        _print_board(p)
    elif cmd == "stats":
        import json
        print(json.dumps(p.stats(), indent=2))
    elif cmd == "add":
        if len(args) < 3:
            sys.exit('usage: add "<name>" <vertical> [source]')
        did = p.add_prospect(args[1], args[2], source=args[3] if len(args) > 3 else "manual")
        if did is None:
            print(f"already in pipeline: {args[1]}")
        else:
            p.save()
            print(f"✓ added [{did}] {args[1]}")
    elif cmd == "advance":
        if len(args) < 3:
            sys.exit("usage: advance <id> <stage> [note]")
        deal = p.advance(args[1], args[2], note=args[3] if len(args) > 3 else None)
        if deal is None:
            print(f"no deal {args[1]}")
        else:
            p.save()
            print(f"✓ {deal['name']} → {deal['stage']}")
    else:
        sys.exit(f"unknown command {cmd!r} — use: list | add | advance | stats")


if __name__ == "__main__":
    main()
