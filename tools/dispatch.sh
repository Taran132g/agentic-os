#!/bin/bash
# dispatch_agent.sh — A2A: spawn another PAIS agent from inside a running task
#
# Usage:
#   ~/agentic_os/tools/dispatch.sh <agent> "<task description>"
#
# Available agents:
#   career, general, finance, briefing, study, content, vault_curator, coding
#
# Examples:
#   ~/agentic_os/tools/dispatch.sh finance "check Dr. Profit signals and write to vault"
#   ~/agentic_os/tools/dispatch.sh vault_curator "cross-link all new crypto notes"
#
# The dispatched task runs in parallel — it does NOT block the current agent.

AGENT="${1:-general}"
TASK="$2"

if [ -z "$TASK" ]; then
  echo '{"ok":false,"error":"Usage: dispatch.sh <agent> \"<task>\""}' >&2
  exit 1
fi

RESULT=$(curl -s -X POST http://localhost:8000/api/dispatch \
  -H "Content-Type: application/json" \
  -d "{\"agent\":\"$AGENT\",\"task\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TASK")}")

echo "$RESULT"
