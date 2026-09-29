#!/bin/bash
# Put Kraken Futures API keys into the Oracle executor's .env without them ever
# touching chat, shell history, or process args (hidden prompt -> ssh stdin).
#   ./set_kraken_keys.sh demo   # KRAKEN_FUTURES_DEMO_API_KEY/SECRET
#   ./set_kraken_keys.sh live   # KRAKEN_FUTURES_API_KEY/SECRET
set -euo pipefail
MODE="${1:-live}"
case "$MODE" in
  demo) PREFIX="KRAKEN_FUTURES_DEMO_API" ;;
  live) PREFIX="KRAKEN_FUTURES_API" ;;
  *) echo "usage: $0 demo|live" >&2; exit 1 ;;
esac
HOST="ubuntu@129.159.182.210"
SSH_KEY="$HOME/.ssh/oracle_pais.key"

read -r -s -p "Kraken ${MODE} API key (hidden): " KEY; echo
read -r -s -p "Kraken ${MODE} private key / secret (hidden): " SECRET; echo
[ -n "$KEY" ] && [ -n "$SECRET" ] || { echo "empty key or secret — aborting" >&2; exit 1; }

printf '%s\n%s\n' "$KEY" "$SECRET" | ssh -i "$SSH_KEY" "$HOST" "PREFIX=$PREFIX /home/ubuntu/agentic-os/.venv/bin/python -c '
import os, re, sys
key, secret = sys.stdin.read().splitlines()[:2]
p = \"/home/ubuntu/agentic-os/.env\"
text = open(p).read()
for name, val in ((os.environ[\"PREFIX\"] + \"_KEY\", key), (os.environ[\"PREFIX\"] + \"_SECRET\", secret)):
    line = f\"{name}={val}\"
    text, n = re.subn(rf\"^{name}=.*$\", lambda _m: line, text, flags=re.M)
    if not n:
        text += f\"\n{line}\n\"
open(p, \"w\").write(text)
print(\"saved\", os.environ[\"PREFIX\"] + \"_KEY/_SECRET to Oracle .env\")
'"
unset KEY SECRET
echo "Next: ssh in and run  cd agentic-os && .venv/bin/python kraken_selftest.py  (read-only check)"
