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

_cache: dict = {"data": None, "ts": 0.0}


def _oauth_token() -> str | None:
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except Exception as e:
        log.warning("usage_quota: cannot read OAuth token: %s", e)
        return None


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


def fetch_quota(force: bool = False) -> dict | None:
    """Return parsed subscription quota, or last-known value on failure.

    Throttled to one live request per _TTL seconds; call freely after each task.
    """
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]

    token = _oauth_token()
    if not token:
        return _cache["data"]

    req = urllib.request.Request(_ENDPOINT, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log.warning("usage_quota: endpoint returned HTTP %s", e.code)
        return _cache["data"]
    except Exception as e:
        log.warning("usage_quota: fetch failed: %s", e)
        return _cache["data"]

    _cache["data"] = _parse(raw)
    _cache["ts"] = now
    return _cache["data"]
