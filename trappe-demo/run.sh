#!/usr/bin/env bash
# Launch the demo with the fast Anthropic API brain.
# Reads the key from the Obsidian vault at runtime — the key is NEVER stored in
# this (public) repo. Falls back to `claude -p` automatically if not found.
set -euo pipefail

VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
KEY=$(grep -oI "sk-ant-[A-Za-z0-9_-]\{20,\}" "$VAULT/Projects & Building/API Keys.md" 2>/dev/null | head -1 || true)

cd "$(dirname "$0")"
if [ -n "$KEY" ]; then
  echo "→ fast brain: Anthropic API"
  ANTHROPIC_API_KEY="$KEY" python3 serve.py
else
  echo "→ no key found; using claude -p"
  python3 serve.py
fi
