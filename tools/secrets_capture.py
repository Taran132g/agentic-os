"""
Move extracted secrets from a browser-agent run into the PAIS keychain and
into project .env files — without letting them sit in chat logs or Telegram.

Typical flow: the PAIS agent drives a dashboard via `tools/pais_browser.py`
+ `tools/computer.py`, extracts secrets (API keys, project URLs, JWT
secrets), then calls save_extracted with the values:

    from tools.secrets_capture import save_extracted

    save_extracted(
        service="supabase:finding-founders",
        account="taran.impact@gmail.com",
        extracted={
            "project_url": "https://xxxxx.supabase.co",
            "anon_key":    "eyJhbGciOi...",
            "jwt_secret":  "long-secret-string",
        },
        env_files={
            "~/FindingFounders/backend/.env": {
                "SUPABASE_JWT_SECRET": "jwt_secret",
            },
            "~/FindingFounders/frontend/.env.local": {
                "NEXT_PUBLIC_SUPABASE_URL":      "project_url",
                "NEXT_PUBLIC_SUPABASE_ANON_KEY": "anon_key",
            },
        },
    )

`env_files` maps a path → {ENV_VAR_NAME: key_in_extracted_dict}.

Every value is also stored in the PAIS keychain as
service="<service>", account="<account>:<extracted_key>" so it can be
re-fetched later without re-running the browser agent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping

from tools import credentials

log = logging.getLogger(__name__)


def _resolve(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p)))


def _merge_env(env_path: Path, updates: Mapping[str, str]) -> tuple[int, int]:
    """
    Update or insert KEY=VALUE lines in env_path. Returns (changed, added).
    Existing other lines are preserved. File is created if missing.
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    remaining = dict(updates)
    changed = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            new_val = remaining.pop(key)
            new_line = f"{key}={new_val}"
            if line != new_line:
                lines[i] = new_line
                changed += 1

    added = 0
    if remaining:
        if lines and lines[-1] != "":
            lines.append("")
        for k, v in remaining.items():
            lines.append(f"{k}={v}")
            added += 1

    env_path.write_text("\n".join(lines) + ("\n" if not lines or lines[-1] != "" else ""))
    # Keep .env private even if the umask leaks.
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    return changed, added


def save_extracted(
    service: str,
    account: str,
    extracted: Mapping[str, str],
    env_files: Mapping[str | Path, Mapping[str, str]] | None = None,
    keychain: bool = True,
) -> dict[str, object]:
    """
    Persist every value in `extracted`. Returns a summary dict.

    Args:
        service: prefix for keychain entries (e.g. "supabase:finding-founders")
        account: account label for keychain entries (usually the email)
        extracted: {key: value} dict returned by the browser agent
        env_files: optional {env_path: {ENV_VAR: extracted_key}} map
        keychain: if False, skip keychain writes (rarely useful)

    Every secret is masked in logs.
    """
    summary: dict[str, object] = {
        "keychain_stored": [],
        "env_updates": {},
        "missing": [],
    }

    if keychain:
        credentials.unlock()
        for key, val in extracted.items():
            if not isinstance(val, str) or not val:
                summary["missing"].append(key)
                continue
            entry_account = f"{account}:{key}"
            credentials.store(
                service=service,
                account=entry_account,
                password=val,
                notes=f"Extracted by PAIS browser agent",
            )
            summary["keychain_stored"].append(f"{service}/{entry_account}")
            log.info("Stored keychain entry %s/%s (len=%d)", service, entry_account, len(val))

    if env_files:
        for raw_path, mapping in env_files.items():
            path = _resolve(raw_path)
            updates: dict[str, str] = {}
            for env_var, extracted_key in mapping.items():
                val = extracted.get(extracted_key)
                if not val:
                    summary["missing"].append(f"{path}:{env_var}<-{extracted_key}")
                    continue
                updates[env_var] = val
            if not updates:
                continue
            changed, added = _merge_env(path, updates)
            summary["env_updates"][str(path)] = {
                "changed": changed,
                "added": added,
                "keys": list(updates.keys()),
            }
            log.info("Updated %s: %d changed, %d added (%s)",
                     path, changed, added, ", ".join(updates.keys()))

    return summary


def fetch(service: str, account: str, key: str) -> str | None:
    """Re-read a previously-saved value from the keychain."""
    credentials.unlock()
    entry = credentials.get(service, account=f"{account}:{key}")
    return entry["password"] if entry else None
