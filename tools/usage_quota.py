"""Claude subscription quota — 5-hour + weekly utilization.

PAIS runs every agent via `claude -p` on Taran's Claude subscription, so the
real constraint isn't dollar cost — it's the subscription rate limits. This
reads the same endpoint the `/usage` slash command uses, authenticated with the
OAuth token Claude Code keeps in the macOS keychain.
"""
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_TTL = 120  # throttle: a burst of finished tasks hits the API at most once / 2 min

_cache: dict = {}   # keyed by account name ("_active" for the logged-in account)


def _token_for(account: str | None) -> str | None:
    """OAuth access token for an account: the live keychain entry for the active
    login, or the saved per-profile entry ('Claude Code-account-<name>') for any
    other account — so we can read a non-active account's quota WITHOUT switching."""
    svc = _KEYCHAIN_SERVICE if not account else f"Claude Code-account-{account}"
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", svc, "-w"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except Exception as e:
        log.warning("usage_quota: cannot read token for %s: %s", account or "active", e)
        return None


def _oauth_token() -> str | None:        # back-compat alias
    return _token_for(None)


def _parse(raw: dict) -> dict:
    def window(d):
        if not isinstance(d, dict):
            return None
        return {
            "utilization": round(d.get("utilization", 0.0), 1),
            "resets_at": d.get("resets_at"),
        }

    eu = raw.get("extra_usage") or {}
    return {
        "five_hour": window(raw.get("five_hour")),
        "seven_day": window(raw.get("seven_day")),
        "extra_usage": {
            "used_credits": eu.get("used_credits"),
            "monthly_limit": eu.get("monthly_limit"),
            "utilization": eu.get("utilization"),
            "currency": eu.get("currency"),
        } if eu.get("is_enabled") else None,
        "fetched_at": time.time(),
    }


def fetch_quota(force: bool = False, account: str | None = None) -> dict | None:
    """Return parsed subscription quota for an account (None = the active login),
    or last-known value on failure. Passing `account` reads that profile's saved
    token so the Control Room can show a non-active account's reset WITHOUT switching.

    Throttled to one live request per _TTL seconds per account.
    """
    key = account or "_active"
    cached = _cache.get(key)
    now = time.time()
    if not force and cached and cached["data"] is not None and now - cached["ts"] < _TTL:
        return cached["data"]

    token = _token_for(account)
    if not token:
        return cached["data"] if cached else None

    req = urllib.request.Request(_ENDPOINT, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log.warning("usage_quota: endpoint returned HTTP %s (acct=%s)", e.code, key)
        return cached["data"] if cached else None
    except Exception as e:
        log.warning("usage_quota: fetch failed (acct=%s): %s", key, e)
        return cached["data"] if cached else None

    parsed = _parse(raw)
    _cache[key] = {"data": parsed, "ts": now}
    return parsed
