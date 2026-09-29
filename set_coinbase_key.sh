#!/bin/bash
# Install Coinbase CDP API credentials on the Oracle executor without them ever
# touching chat, shell history, or process args (hidden prompt -> ssh stdin),
# then run the read-only self-test there (the key is IP-allowlisted to Oracle).
#   ./set_coinbase_key.sh
set -euo pipefail
HOST="ubuntu@129.159.182.210"
SSH_KEY="$HOME/.ssh/oracle_pais.key"

read -r -s -p "Coinbase API key ID (hidden): " KEY; echo
read -r -s -p "Coinbase API secret (hidden): " SECRET; echo
[ -n "$KEY" ] && [ -n "$SECRET" ] || { echo "empty key or secret — aborting" >&2; exit 1; }

printf '%s\n%s\n' "$KEY" "$SECRET" | ssh -i "$SSH_KEY" "$HOST" "cd /home/ubuntu/agentic-os && .venv/bin/python -c '
import re, sys
key, secret = sys.stdin.read().splitlines()[:2]
text = open(\".env\").read()
for name, val in ((\"COINBASE_API_KEY\", key), (\"COINBASE_API_SECRET\", secret)):
    line = f\"{name}={val}\"
    text, n = re.subn(rf\"^{name}=.*$\", lambda _m: line, text, flags=re.M)
    if not n:
        text += f\"\n{line}\n\"
open(\".env\", \"w\").write(text)
print(\"saved COINBASE_API_KEY/SECRET to Oracle .env\")
' && chmod 600 .env && .venv/bin/python coinbase_selftest.py"
unset KEY SECRET
