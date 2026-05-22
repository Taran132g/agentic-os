"""
Dev utilities for the coding agent.

Static analysis: ruff (Python linting), mypy (type checking)
Test runner:     pytest
Git helpers:     status, diff, branch, log
Codebase recon:  file tree, language stats
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PYTHON = sys.executable  # same interpreter that runs PAIS


# ── Subprocess helper ─────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Optional[str] = None, timeout: int = 60) -> dict:
    """Run a subprocess and return {ok, stdout, stderr, returncode}."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Timed out after {timeout}s", "returncode": -1}
    except FileNotFoundError as e:
        return {"ok": False, "stdout": "", "stderr": f"Command not found: {e}", "returncode": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1}


# ── Static analysis ───────────────────────────────────────────────────────────

def run_ruff(path: str, fix: bool = False) -> dict:
    """
    Run ruff linter on a file or directory.
    Returns {ok, issues: str, count: int}.
    Set fix=True to auto-fix safe issues.
    """
    cmd = [_PYTHON, "-m", "ruff", "check", path, "--output-format", "text"]
    if fix:
        cmd.append("--fix")
    result = _run(cmd, timeout=30)
    output = result["stdout"] or result["stderr"]
    lines = [l for l in output.splitlines() if l.strip()]
    issue_lines = [l for l in lines if ": " in l and ("E" in l or "W" in l or "F" in l)]
    return {
        "ok": result["ok"],
        "issues": output[:3000],
        "count": len(issue_lines),
        "fixed": fix and result["ok"],
    }


def run_mypy(path: str) -> dict:
    """
    Run mypy type checker on a file or directory.
    Returns {ok, issues: str, error_count: int}.
    """
    cmd = [_PYTHON, "-m", "mypy", path, "--ignore-missing-imports", "--no-error-summary"]
    result = _run(cmd, timeout=60)
    output = (result["stdout"] or result["stderr"])[:3000]
    error_count = output.count(": error:")
    return {
        "ok": result["ok"],
        "issues": output,
        "error_count": error_count,
    }


# ── Test runner ───────────────────────────────────────────────────────────────

def run_pytest(path: str, test_path: Optional[str] = None, timeout: int = 120) -> dict:
    """
    Run pytest in a directory, optionally targeting a specific test file/path.
    Returns {ok, summary: str, passed: int, failed: int, errors: int}.
    """
    cmd = [_PYTHON, "-m", "pytest", "-v", "--tb=short", "--no-header"]
    if test_path:
        cmd.append(test_path)
    else:
        cmd.append(path)

    result = _run(cmd, cwd=path, timeout=timeout)
    output = result["stdout"] + result["stderr"]

    # Parse pytest output for counts
    passed = failed = errors = 0
    for line in output.splitlines():
        if " passed" in line:
            try:
                passed = int(line.split(" passed")[0].rsplit(" ", 1)[-1])
            except ValueError:
                pass
        if " failed" in line:
            try:
                failed = int(line.split(" failed")[0].rsplit(" ", 1)[-1])
            except ValueError:
                pass
        if " error" in line:
            try:
                errors = int(line.split(" error")[0].rsplit(" ", 1)[-1])
            except ValueError:
                pass

    return {
        "ok": result["returncode"] == 0,
        "summary": output[:4000],
        "passed": passed,
        "failed": failed,
        "errors": errors,
    }


# ── Git helpers ───────────────────────────────────────────────────────────────

def git_status(repo_path: str) -> str:
    """Return `git status --short` output."""
    return _run(["git", "status", "--short"], cwd=repo_path)["stdout"] or "clean"


def git_diff(repo_path: str, staged: bool = False) -> str:
    """Return git diff output (capped at 8000 chars)."""
    cmd = ["git", "diff"] + (["--staged"] if staged else [])
    out = _run(cmd, cwd=repo_path)["stdout"]
    return out[:8000] if out else "(no changes)"


def git_log(repo_path: str, n: int = 10) -> str:
    """Return last N commit messages."""
    result = _run(
        ["git", "log", f"-{n}", "--oneline", "--no-decorate"],
        cwd=repo_path,
    )
    return result["stdout"] or "(no commits)"


def git_create_branch(branch_name: str, repo_path: str) -> dict:
    """Create and checkout a new branch."""
    result = _run(["git", "checkout", "-b", branch_name], cwd=repo_path)
    return {"ok": result["ok"], "message": result["stdout"] or result["stderr"]}


def git_add_commit(repo_path: str, message: str, files: Optional[list[str]] = None) -> dict:
    """Stage files and create a commit. Stages all changes if files is None."""
    stage_cmd = ["git", "add"] + (files if files else ["-A"])
    stage = _run(stage_cmd, cwd=repo_path)
    if not stage["ok"]:
        return {"ok": False, "message": f"git add failed: {stage['stderr']}"}

    commit = _run(["git", "commit", "-m", message], cwd=repo_path)
    return {
        "ok": commit["ok"],
        "message": commit["stdout"] or commit["stderr"],
    }


# ── Codebase recon ────────────────────────────────────────────────────────────

def file_tree(path: str, depth: int = 3, max_files: int = 150) -> str:
    """
    Return a text file tree of a directory.
    Skips common noise dirs: __pycache__, .git, node_modules, .venv, dist, build.
    """
    SKIP = {
        "__pycache__", ".git", "node_modules", ".venv", "venv", "env",
        "dist", "build", ".next", ".nuxt", "coverage", ".pytest_cache",
        "chroma_db", ".obsidian",
    }

    root = Path(path)
    if not root.exists():
        return f"Path not found: {path}"

    lines: list[str] = [str(root.name) + "/"]
    count = 0

    def _walk(p: Path, indent: int, current_depth: int):
        nonlocal count
        if current_depth > depth or count >= max_files:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in SKIP or entry.name.startswith("."):
                continue
            prefix = "  " * indent
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                _walk(entry, indent + 1, current_depth + 1)
            else:
                lines.append(f"{prefix}{entry.name}")
                count += 1
                if count >= max_files:
                    lines.append(f"{prefix}… (truncated at {max_files} files)")
                    return

    _walk(root, 1, 1)
    return "\n".join(lines)


def language_stats(path: str) -> str:
    """Return a brief language breakdown for a directory."""
    root = Path(path)
    ext_counts: dict[str, int] = {}
    SKIP = {"__pycache__", ".git", "node_modules", ".venv", "venv"}

    for f in root.rglob("*"):
        if any(s in f.parts for s in SKIP):
            continue
        if f.is_file() and f.suffix:
            ext_counts[f.suffix] = ext_counts.get(f.suffix, 0) + 1

    if not ext_counts:
        return "No files found."

    sorted_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)
    lines = [f"  {ext}: {count}" for ext, count in sorted_exts[:12]]
    return "\n".join(lines)


# ── Convenience: full pre-check ───────────────────────────────────────────────

def quick_health_check(path: str) -> str:
    """Run ruff + pytest and return a combined summary string for the agent prompt."""
    lines: list[str] = [f"## Health Check: {path}"]

    ruff = run_ruff(path)
    lines.append(f"\n### Ruff (lint)")
    lines.append(f"Issues: {ruff['count']}")
    if ruff["count"]:
        lines.append(ruff["issues"][:1000])

    pytest_result = run_pytest(path)
    lines.append(f"\n### Pytest")
    lines.append(
        f"Passed: {pytest_result['passed']}  Failed: {pytest_result['failed']}  Errors: {pytest_result['errors']}"
    )
    if pytest_result["failed"] or pytest_result["errors"]:
        lines.append(pytest_result["summary"][:1500])

    return "\n".join(lines)
