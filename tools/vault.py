"""Obsidian vault read/write — always writes via filesystem, never MCP (iCloud sync)."""

import os
from pathlib import Path
from config import VAULT_PATH


def _resolve(relative_path: str) -> Path:
    path = VAULT_PATH / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read(relative_path: str) -> str:
    path = _resolve(relative_path)
    if not path.exists():
        return f"[File not found: {relative_path}]"
    return path.read_text(encoding="utf-8")


def write(relative_path: str, content: str) -> str:
    path = _resolve(relative_path)
    path.write_text(content, encoding="utf-8")
    return f"Written: {relative_path}"


def append(relative_path: str, content: str) -> str:
    path = _resolve(relative_path)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + content)
    return f"Appended to: {relative_path}"


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
