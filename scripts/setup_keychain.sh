#!/usr/bin/env bash
# One-time setup for the PAIS no-prompt credential keychain.
# Creates a custom macOS keychain that PAIS can read/write WITHOUT triggering
# system password prompts. Master password is stored in ~/agentic_os/.keychain_pass
# (mode 0600) so PAIS can unlock the keychain unattended at startup.
#
# Re-running this script is safe — it will not clobber an existing keychain.

set -euo pipefail

PAIS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEYCHAIN="$PAIS_DIR/pais.keychain-db"
PASSFILE="$PAIS_DIR/.keychain_pass"

if [ -f "$PASSFILE" ]; then
    echo "[setup_keychain] Found existing master password at $PASSFILE"
    PASS="$(cat "$PASSFILE")"
else
    echo "[setup_keychain] Generating new master password at $PASSFILE"
    # Generate 48 chars from /dev/urandom; tolerate SIGPIPE from `head` closing.
    PASS="$(set +o pipefail; LC_ALL=C tr -dc 'A-Za-z0-9!@#%^&_=+-' </dev/urandom 2>/dev/null | head -c 48)"
    umask 077
    printf '%s' "$PASS" > "$PASSFILE"
    chmod 600 "$PASSFILE"
fi

if [ ! -f "$KEYCHAIN" ]; then
    echo "[setup_keychain] Creating keychain at $KEYCHAIN"
    security create-keychain -p "$PASS" "$KEYCHAIN"
else
    echo "[setup_keychain] Keychain already exists at $KEYCHAIN"
fi

# No auto-lock, no lock on sleep — stays unlocked forever once unlocked.
security set-keychain-settings "$KEYCHAIN"

# Unlock it now.
security unlock-keychain -p "$PASS" "$KEYCHAIN"

# Add to the user's keychain search list (idempotent — collect current list,
# dedupe, append ours if missing).
CURRENT="$(security list-keychains -d user | sed -e 's/^[[:space:]]*//' -e 's/"//g')"
if ! echo "$CURRENT" | grep -qF "$KEYCHAIN"; then
    echo "[setup_keychain] Adding to user keychain search list"
    # shellcheck disable=SC2086
    security list-keychains -d user -s $CURRENT "$KEYCHAIN"
fi

echo "[setup_keychain] Done."
echo "  Keychain:        $KEYCHAIN"
echo "  Master password: $PASSFILE (mode 600)"
echo
echo "PAIS will unlock this keychain on every startup automatically."
