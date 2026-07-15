"""clip_sheet.py — single source of truth for the clipping pipeline.

Mirrors the job_sheet.py pattern: an editable markdown table in the Obsidian
vault. clip_pipeline.py APPENDS newly-rendered clips, Taran flips status as he
posts/submits (in the note or via `clip_pipeline.py mark`), and every PAIS
reader/writer goes through THIS module.

Status flow:  🎬 Rendered → 📱 Posted → 📤 Submitted → 💰 Paid
Terminal:     ⚪ Skip  (bad clip, never posted)

Design notes
------------
* Clip file stem is the row key (unique: <video_id>_c<n>).
* Vault reads go through tools.icloud_read (eviction guard — never read raw).
* Writes are atomic (tmp + os.replace); append never rewrites an existing row.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from tools import icloud_read

VAULT = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
         "Documents" / "Digital Brain")
SHEET = VAULT / "Projects & Building" / "Clip Pipeline.md"

BELOW = "<!-- CLIP_AGENT_APPEND_BELOW"
ABOVE = "<!-- CLIP_AGENT_APPEND_ABOVE -->"

STATUSES = ["🎬 Rendered", "📱 Posted", "📤 Submitted", "💰 Paid", "⚪ Skip"]
DEFAULT_STATUS = STATUSES[0]

_HEADER = (
    "| Status | Date | Creator | Hook | Clip | Post | Notes |\n"
    "|--------|------|---------|------|------|------|-------|"
)

_SCAFFOLD = """---
type: pipeline
tags:
  - content
  - clipping
updated: {today}
---

# Clip Pipeline

Live tracker for the clipping operation. The **Clips agent**
(`~/agentic_os/clip_pipeline.py daily`) renders new clips from watched creators
and appends them below as `🎬 Rendered`. Flip status as you post and submit —
here, or via `python3 clip_pipeline.py mark <clip> posted|submitted <url>`.

| Code | Meaning |
|------|---------|
| 🎬 Rendered | Cut + captioned, sitting in ~/Desktop/Clips/ |
| 📱 Posted | Live on TikTok/IG/Shorts |
| 📤 Submitted | Link submitted to the campaign |
| 💰 Paid | Payout received |
| ⚪ Skip | Bad clip — not posting |

## Campaigns (sign up / submit here)

| Creator | Program | Link | Rate | Joined |
|---------|---------|------|------|--------|
| Togi | Togi ClipVault (Whop) | https://whop.com/togi-clipvault/ | check page | ☐ |
| Whop Originals | Whop Clips | https://whop.com/whop-creators-ugc/whop-clips/ | check page | ☐ |
| — browse | Whop Content Rewards | https://whop.com/discover/content-rewards/ | $1–5/1K | ☐ |
| — browse | Vyro (MrBeast) | https://app.vyro.com/sign-up | ~$3/1K, hourly payout | ☐ |
| — browse | Clipping.io | https://clipping.io/ | varies | ☐ |
| — browse | Reach.cat | https://reach.cat/ | $1–6/1K, weekly | ☐ |
| StableRonaldo | verify on Whop/Discord | https://whop.com/discover/content-rewards/ | ? | ☐ |
| TJR | verify on Whop | https://whop.com/discover/content-rewards/ | ? | ☐ |

## Clips

{below} — the Clips agent appends rendered clips between these markers; safe to edit any cell -->
{header}
{above}

---

**Related:** [[Whop Clipping Playbook]] · [[Clip Target Checklist]] · [[Content Creation]]
"""


def _ensure_sheet() -> None:
    if SHEET.exists():
        return
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(_SCAFFOLD.format(
        today=datetime.now().strftime("%Y-%m-%d"),
        below=BELOW, header=_HEADER, above=ABOVE,
    ))


def _atomic_write(text: str) -> None:
    tmp = SHEET.with_suffix(".md.tmp")
    tmp.write_text(text)
    os.replace(tmp, SHEET)


def _esc(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ").strip()


def append(creator: str, hook: str, clip_stem: str, notes: str = "") -> bool:
    """Add a rendered clip row. Returns False if the clip is already tracked."""
    _ensure_sheet()
    text = icloud_read.read_text(SHEET)
    if f"| {clip_stem} |" in text or f"`{clip_stem}`" in text:
        return False
    row = (f"| {DEFAULT_STATUS} | {datetime.now().strftime('%Y-%m-%d')} "
           f"| {_esc(creator)} | {_esc(hook)} | `{clip_stem}` | — | {_esc(notes)} |")
    lines = text.splitlines(keepends=True)
    out, inserted = [], False
    for line in lines:
        out.append(line)
        if not inserted and ABOVE in line:
            # insert BEFORE the ABOVE marker → newest at bottom
            out.insert(len(out) - 1, row + "\n")
            inserted = True
    if not inserted:
        return False
    _atomic_write("".join(out))
    return True


def mark(clip_stem: str, status: str, post_url: str | None = None) -> bool:
    """Update a clip row's status (and post URL when provided)."""
    _ensure_sheet()
    matched = [s for s in STATUSES if status.lower() in s.lower()]
    if not matched:
        raise ValueError(f"status must be one of {STATUSES}")
    new_status = matched[0]
    out, hit = [], False
    for line in icloud_read.read_text(SHEET).splitlines(keepends=True):
        if f"`{clip_stem}`" in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 7:
                cells[0] = new_status
                if post_url:
                    cells[5] = post_url
                line = "| " + " | ".join(cells) + " |\n"
                hit = True
        out.append(line)
    if hit:
        _atomic_write("".join(out))
    return hit


def rows(status_filter: str | None = None) -> list[dict]:
    """All tracked clip rows (optionally filtered by status substring)."""
    _ensure_sheet()
    result, in_table = [], False
    for line in icloud_read.read_text(SHEET).splitlines():
        if BELOW in line:
            in_table = True
            continue
        if ABOVE in line:
            break
        if not in_table or not line.strip().startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or cells[0] == "Status":
            continue
        row = {"status": cells[0], "date": cells[1], "creator": cells[2],
               "hook": cells[3], "clip": cells[4].strip("`"), "post": cells[5],
               "notes": cells[6]}
        if status_filter is None or status_filter.lower() in row["status"].lower():
            result.append(row)
    return result
