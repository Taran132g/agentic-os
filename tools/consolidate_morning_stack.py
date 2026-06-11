#!/usr/bin/env python3
"""consolidate_morning_stack.py — one-shot n8n DB migration (2026-06-09).

Collapses the staggered PAIS schedules into a single 07:30 chained run:
  * vault-daily-digest  -> renamed 'morning-stack', cron 30 7 * * *, runs
                           morning_stack.sh (which chains every job + sleeps).
  * email-triage / apply-jobs / piontrix-outreach / brainscan-outreach:
                           their scheduleTrigger node(s) are DISABLED so the cron
                           no longer fires, but the workflow stays active so its
                           manual webhook keeps working.
  * repo-sync / power-sleep: deactivated (schedule-only, now handled in the stack
                           + power_cycle.py).

Run with the n8n service STOPPED. Idempotent.
"""
import json
import sqlite3
from pathlib import Path

DB = Path.home() / ".n8n" / "database.sqlite"
SCRIPT_CMD = "bash /Users/taranveersingh/agentic_os/morning_stack.sh"
MORNING_CRON = "30 7 * * *"

DISABLE_SCHEDULE = ["email-triage", "apply-jobs", "piontrix-outreach", "brainscan-outreach"]
DEACTIVATE = ["repo-sync", "power-sleep"]


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1) Repurpose vault-daily-digest -> morning-stack
    row = cur.execute(
        "SELECT id, nodes FROM workflow_entity WHERE name='vault-daily-digest'"
    ).fetchone()
    if row:
        nodes = json.loads(row["nodes"])
        for n in nodes:
            t = n.get("type", "")
            if t == "n8n-nodes-base.scheduleTrigger":
                n["parameters"]["rule"] = {
                    "interval": [{"field": "cronExpression", "expression": MORNING_CRON}]
                }
                n.pop("disabled", None)  # ensure enabled
            elif t == "n8n-nodes-base.executeCommand":
                n["parameters"]["command"] = SCRIPT_CMD
        cur.execute(
            "UPDATE workflow_entity SET name='morning-stack', nodes=?, active=1 WHERE id=?",
            (json.dumps(nodes), row["id"]),
        )
        print(f"✓ vault-daily-digest -> morning-stack (cron {MORNING_CRON}, active)")
    else:
        print("• vault-daily-digest not found (already migrated?)")

    # 2) Disable schedule trigger nodes (keep workflow active for webhooks)
    for name in DISABLE_SCHEDULE:
        r = cur.execute(
            "SELECT id, nodes FROM workflow_entity WHERE name=?", (name,)
        ).fetchone()
        if not r:
            print(f"• {name} not found")
            continue
        nodes = json.loads(r["nodes"])
        n_disabled = 0
        for n in nodes:
            if n.get("type") == "n8n-nodes-base.scheduleTrigger":
                n["disabled"] = True
                n_disabled += 1
        cur.execute("UPDATE workflow_entity SET nodes=? WHERE id=?", (json.dumps(nodes), r["id"]))
        print(f"✓ {name}: disabled {n_disabled} schedule node(s) (still active for webhook)")

    # 3) Deactivate schedule-only workflows
    for name in DEACTIVATE:
        cur.execute("UPDATE workflow_entity SET active=0 WHERE name=?", (name,))
        print(f"✓ {name}: deactivated")

    con.commit()
    print("\n=== final state ===")
    for r in cur.execute(
        "SELECT name, active FROM workflow_entity ORDER BY active DESC, name"
    ):
        print(f"  {'ON ' if r['active'] else 'off'}  {r['name']}")
    con.close()


if __name__ == "__main__":
    main()
