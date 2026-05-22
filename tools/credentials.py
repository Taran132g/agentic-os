"""
PAIS credential store — no-prompt access to passwords via a custom macOS keychain.

The keychain lives at ~/agentic_os/pais.keychain-db and is unlocked at PAIS
startup using a master password stored in ~/agentic_os/.keychain_pass (mode 0600).
Because the keychain is custom (not the user's login keychain) and unlocked
for the lifetime of the PAIS process, reads and writes go through without any
macOS security prompts.

Run scripts/setup_keychain.sh once before using this module.
"""

from __future__ import annotations

import logging
import os
import secrets
import string
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PAIS_DIR = Path(__file__).resolve().parent.parent
KEYCHAIN_PATH = str(_PAIS_DIR / "pais.keychain-db")
_PASSFILE = _PAIS_DIR / ".keychain_pass"

_unlocked = False


class CredentialError(RuntimeError):
    pass


def _master_password() -> str:
    pw = os.environ.get("PAIS_KEYCHAIN_PASS")
    if pw:
        return pw
    if not _PASSFILE.exists():
        raise CredentialError(
            f"No keychain master password found. Run scripts/setup_keychain.sh first."
        )
    return _PASSFILE.read_text().strip()


def _run(args: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run `security` command. Never echoes secrets to stdout/stderr logs."""
    return subprocess.run(
        args,
        check=check,
        capture_output=capture,
        text=True,
    )


def unlock() -> None:
    """Unlock the PAIS keychain. Idempotent — safe to call repeatedly."""
    global _unlocked
    if not Path(KEYCHAIN_PATH).exists():
        raise CredentialError(
            f"Keychain not found at {KEYCHAIN_PATH}. Run scripts/setup_keychain.sh."
        )
    pw = _master_password()
    _run(["security", "unlock-keychain", "-p", pw, KEYCHAIN_PATH])
    # Disable auto-lock so it stays unlocked.
    _run(["security", "set-keychain-settings", KEYCHAIN_PATH])
    _unlocked = True
    log.info("PAIS keychain unlocked")


def _ensure_unlocked() -> None:
    if not _unlocked:
        unlock()


def store(service: str, account: str, password: str, notes: Optional[str] = None) -> None:
    """Store a credential. Overwrites any existing entry for (service, account)."""
    _ensure_unlocked()
    args = [
        "security", "add-generic-password",
        "-s", service,
        "-a", account,
        "-w", password,
        "-U",  # update if exists
        KEYCHAIN_PATH,
    ]
    if notes:
        args[-1:-1] = ["-j", notes]
    _run(args)
    log.info("Stored credential service=%s account=%s", service, account)


def get_password(service: str, account: Optional[str] = None) -> Optional[str]:
    """Return password for (service[, account]) or None if not found."""
    _ensure_unlocked()
    args = ["security", "find-generic-password", "-s", service]
    if account:
        args += ["-a", account]
    args += ["-w", KEYCHAIN_PATH]
    result = _run(args, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def get(service: str, account: Optional[str] = None) -> Optional[dict]:
    """Return {service, account, password, notes} for a stored credential, or None."""
    _ensure_unlocked()
    args = ["security", "find-generic-password", "-s", service]
    if account:
        args += ["-a", account]
    args += ["-g", KEYCHAIN_PATH]
    result = _run(args, check=False)
    if result.returncode != 0:
        return None
    # `security ... -g` prints the metadata to stdout AND the password to stderr.
    meta = result.stdout
    err = result.stderr
    pw = None
    for line in err.splitlines():
        if line.startswith("password:"):
            raw = line[len("password:"):].strip()
            if raw.startswith('"') and raw.endswith('"'):
                pw = raw[1:-1]
            elif raw.startswith("0x"):
                hex_part = raw.split()[0][2:]
                pw = bytes.fromhex(hex_part).decode("utf-8", errors="replace")
            else:
                pw = raw
            break
    acct = account
    notes = None
    for line in meta.splitlines():
        line = line.strip()
        if line.startswith('"acct"<blob>='):
            acct = _parse_attr(line)
        elif line.startswith('"gena"<blob>=') or line.startswith('0x00000008 <blob>='):
            notes = _parse_attr(line)
    return {"service": service, "account": acct or "", "password": pw or "", "notes": notes or None}


def _parse_attr(line: str) -> str:
    val = line.split("=", 1)[1].strip()
    if val == "<NULL>":
        return ""
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("0x"):
        hex_part = val.split()[0][2:]
        try:
            return bytes.fromhex(hex_part).decode("utf-8", errors="replace")
        except ValueError:
            return val
    return val


def delete(service: str, account: Optional[str] = None) -> bool:
    """Delete a credential. Returns True if something was deleted."""
    _ensure_unlocked()
    args = ["security", "delete-generic-password", "-s", service]
    if account:
        args += ["-a", account]
    args += [KEYCHAIN_PATH]
    result = _run(args, check=False)
    return result.returncode == 0


def list_services() -> list[dict]:
    """Return a list of {service, account} for every stored credential."""
    _ensure_unlocked()
    result = _run(["security", "dump-keychain", KEYCHAIN_PATH], check=False)
    if result.returncode != 0:
        return []
    entries: list[dict] = []
    current: dict = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("keychain:"):
            if current.get("service"):
                entries.append(current)
            current = {}
        elif line.startswith('"svce"<blob>='):
            current["service"] = _parse_attr(line)
        elif line.startswith('"acct"<blob>='):
            current["account"] = _parse_attr(line)
    if current.get("service"):
        entries.append(current)
    # Dedupe (service, account) preserving order.
    seen = set()
    out = []
    for e in entries:
        key = (e.get("service", ""), e.get("account", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"service": e.get("service", ""), "account": e.get("account", "")})
    return out


def generate_password(length: int = 24) -> str:
    """Generate a strong random password for new sign-ups."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))
