"""Obsidian vault read/write — always writes via filesystem, never MCP (iCloud sync)."""

import os
import subprocess
from pathlib import Path
from config import VAULT_PATH


def _resolve(relative_path: str) -> Path:
    # We still need the Path object for existence checks, but we'll use string paths for Bash
    return VAULT_PATH / relative_path


def read(relative_path: str) -> str:
    path = _resolve(relative_path)
    if not path.exists():
        return f"[File not found: {relative_path}]"
    return path.read_text(encoding="utf-8")


def write(relative_path: str, content: str) -> str:
    path = _resolve(relative_path)
    # Use Bash to write file to avoid iCloud sync issues
    try:
        subprocess.run([
            "bash", "-c",
            f"mkdir -p \"$(dirname \"$BASE/{relative_path}\")\" && cat > \"$BASE/{relative_path}\" << 'EOF'\n{content}\nEOF\n"
        ], check=True, env={**os.environ, "BASE": str(VAULT_PATH)})
        return f"Written: {relative_path}"
    except Exception as e:
        return f"Error writing {relative_path}: {e}"


def append(relative_path: str, content: str) -> str:
    path = _resolve(relative_path)
    # Use Bash to append to file to avoid iCloud sync issues
    try:
        # We use printf to append with a newline
        subprocess.run([
            "bash", "-c",
            f"mkdir -p \"$(dirname \"$BASE/{relative_path}\")\" && printf \"\\n%s\" \"{content}\" >> \"$BASE/{relative_path}\""
        ], check=True, env={**os.environ, "BASE": str(VAULT_PATH)})
        return f"Appended to: {relative_path}"
    except Exception as e:
        return f"Error appending to {relative_path}: {e}"


def list_dir(relative_path: str) -> str:
    path = VAULT_PATH / relative_path if relative_path else VAULT_PATH
    if not path.exists():
        return f"[Directory not found: {relative_path}]"
    entries = sorted(path.iterdir())
    lines = []
    for e in entries:
        prefix = "📁 " if e.is_dir() else "📄 "
        lines.append(f"{prefix}{e.name}")
    return "\n".join(lines) if lines else "(empty)"
