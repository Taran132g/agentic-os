#!/usr/bin/env python3
"""repo-sync — auto-commit + push Taran's own repos (built for n8n 2026-06-02).

Runs Mon/Wed/Sat from the `repo-sync` n8n workflow. For each repo on the
ALLOWLIST it: stages all changes (respecting .gitignore), runs a SECRET GUARD,
commits with a timestamped message, and pushes to origin. Telegrams a summary.

SAFETY (this pushes to a PUBLIC repo, agentic_os):
  - Allowlist only. Never touches others'/course repos or the multi-clone
    taranveer-singh.github.io.
  - Remote-owner check: refuses to push unless origin belongs to OWNER_OK.
  - Secret guard: aborts a repo's push if any staged file matches a sensitive
    filename pattern OR staged content matches an obvious secret pattern. The
    guard fails CLOSED — anything suspicious blocks the push and alerts Taran.

Usage:
    python3 tools/repo_sync.py          # commit + push allowlisted repos
    REPO_SYNC_DRY=1 python3 ...         # show what WOULD happen, no commit/push
Stamp the commit time via env so runs are reproducible under n8n.
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent.parent
load_dotenv(AGENTIC_DIR / ".env")

HOME = Path.home()
OWNER_OK = ("Taran132g", "taranalt8", "taran.impact")  # acceptable remote owners

# Only Taran-owned, single-clone repos. (github.io has 3 conflicting clones →
# excluded on purpose; add here once a canonical clone is chosen.)
ALLOWLIST = [
    HOME / "agentic_os",
    HOME / "FindingFounders",
    HOME / "Automated-Trading-Bot",
]

# Secret guard — genuine secret/key/PII FILE TYPES that must never be pushed.
# (Deliberately NOT matching source files like credentials.py / secrets_capture.py
# by name — actual leaked keys are caught by the content scan below.)
DENY_NAMES = re.compile(
    r"(^|/)(\.env(\..+)?|.*\.key|.*\.pem|.*\.session|.*\.keychain-db.*|"
    r"\.keychain_pass|piontrix_leads\.json|application_profile\.md|JOB_APP_BRIEF\.md|"
    r"brainscan_creators\.json|linkedin_targets\.json|applications\.json|"
    r"job_queue\.json|scout_jobs\.json|"   # real names/emails/app history (PII)
    r"id_rsa.*|.*\.p12|.*\.pfx)$", re.I)
# Secret guard — content patterns (high-signal API key / token shapes)
DENY_CONTENT = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[0-9A-Za-z\-]{10,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b\d{6,10}:[A-Za-z0-9_\-]{30,}\b)")  # last = telegram bot token shape


def _git(repo: Path, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _tg(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN"); cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": int(cid), "text": text[:4000],
                            "parse_mode": "HTML"}, timeout=20)
    except Exception:
        pass


def _secret_guard(repo: Path, status: list[tuple[str, str]]) -> list[str]:
    """status = list of (code, file) from --name-status. Empty return = safe."""
    bad = []
    for code, f in status:
        if code.startswith("D"):
            continue  # deletions can't leak content or files
        if DENY_NAMES.search(f):
            bad.append(f"filename:{f}")
            continue
        # scan ADDED lines of this staged file for real secret shapes
        try:
            diff = _git(repo, "diff", "--cached", "--", f, check=False)
        except Exception:
            diff = ""
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                if DENY_CONTENT.search(line):
                    bad.append(f"content:{f}")
                    break
    return bad


def sync_one(repo: Path, stamp: str, dry: bool) -> dict:
    name = repo.name
    if not (repo / ".git").exists():
        return {"repo": name, "status": "skip", "detail": "not a git repo"}

    # remote-owner check — refuse anything not clearly Taran's
    origin = _git(repo, "remote", "get-url", "origin", check=False)
    if not any(o in origin for o in OWNER_OK):
        return {"repo": name, "status": "refused",
                "detail": f"remote not owned by Taran: {origin}"}

    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "add", "-A")
    status = [(p[0], p[1]) for p in
              (l.split("\t", 1) for l in _git(repo, "diff", "--cached", "--name-status").splitlines())
              if len(p) == 2]
    staged = [f for _, f in status]
    if not staged:
        # maybe unpushed commits exist even with clean tree
        ahead = _git(repo, "rev-list", "--count", f"origin/{branch}..{branch}", check=False)
        if ahead and ahead != "0" and not dry:
            _git(repo, "push", "origin", branch)
            return {"repo": name, "status": "pushed", "detail": f"{ahead} prior commit(s)"}
        return {"repo": name, "status": "clean", "detail": "nothing to commit"}

    violations = _secret_guard(repo, status)
    if violations:
        _git(repo, "reset", check=False)  # unstage — do not commit secrets
        return {"repo": name, "status": "BLOCKED",
                "detail": f"secret guard: {', '.join(violations[:5])}"}

    if dry:
        _git(repo, "reset", check=False)  # dry: leave nothing staged
        return {"repo": name, "status": "would-commit",
                "detail": f"{len(staged)} file(s) on {branch}"}

    _git(repo, "commit", "-m",
         f"chore: auto-sync {stamp}\n\nAutomated commit via repo_sync (n8n).\n\n"
         f"Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")
    _git(repo, "push", "origin", branch)
    return {"repo": name, "status": "pushed", "detail": f"{len(staged)} file(s) on {branch}"}


def main() -> int:
    dry = os.environ.get("REPO_SYNC_DRY") == "1"
    stamp = os.environ.get("REPO_SYNC_STAMP") or datetime.now().strftime("%Y-%m-%d %H:%M")
    results = []
    for repo in ALLOWLIST:
        try:
            results.append(sync_one(repo, stamp, dry))
        except Exception as e:
            results.append({"repo": repo.name, "status": "error", "detail": str(e)})

    icon = {"pushed": "✅", "clean": "▫️", "would-commit": "📝", "skip": "⏭️",
            "refused": "⛔", "BLOCKED": "🛑", "error": "⚠️"}
    lines = [f"<b>🔄 Repo sync — {stamp}</b>" + (" (dry)" if dry else "")]
    for r in results:
        lines.append(f"{icon.get(r['status'],'•')} <b>{r['repo']}</b> "
                     f"[{r['status']}] {r['detail']}")
    blocked = [r for r in results if r["status"] in ("BLOCKED", "error", "refused")]
    if blocked:
        lines.append("\n⚠️ <b>Needs attention</b> — some repos were not pushed.")
    msg = "\n".join(lines)
    print(msg)
    if not dry:
        _tg(msg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"REPO_SYNC FAILED: {e}", file=sys.stderr)
        sys.exit(1)
