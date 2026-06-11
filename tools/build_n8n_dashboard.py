#!/usr/bin/env python3
"""build_n8n_dashboard.py — regenerate dashboard/n8n_data.js from the n8n DB.

Extracts every workflow (nodes, connections, schedules, commands, webhooks) plus
14-day execution stats from ~/.n8n/database.sqlite and writes them as a JS
global for the static viewer at dashboard/n8n.html.

Run after editing workflows so the dashboard stays current:
    python3 ~/agentic_os/tools/build_n8n_dashboard.py
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path.home() / ".n8n" / "database.sqlite"
OUT = Path(__file__).resolve().parent.parent / "dashboard" / "n8n_data.js"
STATS_DAYS = 14
RECENT_LIMIT = 12


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    workflows = []
    for wid, name, active, nodes, conns, updated in cur.execute(
            "SELECT id, name, active, nodes, connections, updatedAt "
            "FROM workflow_entity ORDER BY active DESC, name"):
        workflows.append({
            "id": wid,
            "name": name,
            "active": bool(active),
            "updatedAt": updated,
            "nodes": json.loads(nodes),
            "connections": json.loads(conns),
        })

    stats = {}
    for name, status, count in cur.execute(
            "SELECT w.name, e.status, COUNT(*) FROM execution_entity e "
            "JOIN workflow_entity w ON e.workflowId = w.id "
            f"WHERE e.startedAt > datetime('now', '-{STATS_DAYS} days') "
            "GROUP BY w.name, e.status"):
        stats.setdefault(name, {})[status] = count

    recent = [
        {"workflow": name, "status": status, "startedAt": started,
         "stoppedAt": stopped}
        for name, status, started, stopped in cur.execute(
            "SELECT w.name, e.status, e.startedAt, e.stoppedAt "
            "FROM execution_entity e "
            "JOIN workflow_entity w ON e.workflowId = w.id "
            f"ORDER BY e.startedAt DESC LIMIT {RECENT_LIMIT}")
    ]
    con.close()

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "statsDays": STATS_DAYS,
        "workflows": workflows,
        "stats": stats,
        "recent": recent,
    }
    OUT.write_text("window.N8N_DATA = "
                   + json.dumps(payload, ensure_ascii=False, indent=1)
                   + ";\n")
    print(f"wrote {OUT} ({len(workflows)} workflows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
