"""job_sheet.py — the single source of truth for Taran's job pipeline.

The pipeline is an editable markdown table in the Obsidian vault (mirrors the
Piontrix Sales Pipeline pattern): the merged Jobs agent APPENDS newly-scouted
roles, the Control Room reads + edits row status, and Taran can hand-edit the
note directly. Every reader/writer in PAIS goes through THIS module so the two
codebases (agentic_os bridge + pais-runtime morning routine) never drift.

Status flow:  🔍 To apply → 📤 Applied → 📞 Interview → 🎯 Offer
Terminal:     ⚫ Rejected / ⚪ Skip  (drop off the active sheet, stay in the note)

Design notes
------------
* URL is the row key — unique per posting, so duplicate-company rows never clash.
* Writes are atomic (tmp + os.replace) so an iCloud sync mid-write can't corrupt.
* append never rewrites an existing row → a status Taran edited is never clobbered.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from tools import icloud_read

VAULT = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
         "Documents" / "Digital Brain")
SHEET = VAULT / "Projects & Building" / "Job Pipeline.md"

BELOW = "<!-- JOB_AGENT_APPEND_BELOW"          # substring match (tolerates trailing notes)
ABOVE = "<!-- JOB_AGENT_APPEND_ABOVE -->"

STATUSES = ["🔍 To apply", "📤 Applied", "📞 Interview", "🎯 Offer", "⚫ Rejected", "⚪ Skip"]
DEFAULT_STATUS = STATUSES[0]
APPLIED_STATUS = STATUSES[1]

_HEADER = (
    "| Status | Company | Role | Location | Match | Applied | Link | Notes | Added | Posted |\n"
    "|--------|---------|------|----------|-------|---------|------|-------|-------|--------|"
)

_SCAFFOLD = f"""---
type: pipeline
updated: {{today}}
---

# Job Pipeline

Your live application tracker. The **Jobs agent** scouts fresh roles and appends
them below as `🔍 To apply`. Change a row's status in the Control Room (or here)
and it saves straight to this note. Rows you mark `⚫ Rejected` / `⚪ Skip` stay
in the note but drop off the active sheet.

| Code | Meaning |
|------|---------|
| 🔍 To apply | Scouted, not yet submitted |
| 📤 Applied | Application submitted |
| 📞 Interview | Heard back — interviewing |
| 🎯 Offer | Offer received |
| ⚫ Rejected | Closed — no |
| ⚪ Skip | Disqualified / not pursuing |

{BELOW} — the Jobs agent inserts new roles between these markers; safe to edit any cell -->
{_HEADER}
{ABOVE}
"""


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def _link_url(cell: str) -> str:
    """Extract the raw URL from a Link cell, whether `[open](url)` or a bare url."""
    m = re.search(r"\((https?://[^)]+)\)", cell or "")
    if m:
        return m.group(1)
    m = re.search(r"https?://\S+", cell or "")
    return m.group(0) if m else ""


def _cell(v: str) -> str:
    """Escape pipes/newlines so a value can't break the markdown table."""
    return str(v or "").replace("|", "\\|").replace("\n", " ").strip()


def _split_cells(line: str) -> list[str]:
    """Split a markdown table row into cells on UNESCAPED pipes, then unescape.

    _cell() writes a literal pipe inside a value as '\\|', so a naive split('|')
    mis-columns any row whose title contains a pipe (e.g. 'New Grad | SWE') — it
    splits the title into two cells, shifting the URL out of column 6 so the row
    can't be matched or status-edited from the Control Room ('Couldn't save')."""
    parts = re.split(r"(?<!\\)\|", line.strip())
    if parts and parts[0].strip() == "":      # drop the leading table delimiter
        parts = parts[1:]
    if parts and parts[-1].strip() == "":      # drop the trailing table delimiter
        parts = parts[:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def ensure_sheet() -> None:
    """Create the note with header + markers if it doesn't exist yet."""
    if SHEET.exists():
        return
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    _write(_SCAFFOLD.format(today=datetime.now().strftime("%Y-%m-%d")))


def _write(text: str) -> None:
    tmp = str(SHEET) + ".tmp"
    Path(tmp).write_text(text, encoding="utf-8")
    os.replace(tmp, str(SHEET))


def rows() -> list[dict]:
    """Parse the pipeline table (between the markers) into row dicts."""
    if not SHEET.exists():
        return []
    out, in_p = [], False
    for line in icloud_read.read_text(SHEET).splitlines():
        s = line.strip()
        if BELOW in s:
            in_p = True
            continue
        if ABOVE in s:
            break
        if not (in_p and s.startswith("|")):
            continue
        cols = _split_cells(s)
        # skip header + separator rows
        if len(cols) < 2 or cols[1].lower() == "company" or re.fullmatch(r"-+", cols[1] or ""):
            continue
        url = _link_url(cols[6] if len(cols) > 6 else "")
        out.append({
            "status": cols[0],
            "company": cols[1],
            "role": cols[2] if len(cols) > 2 else "",
            "location": cols[3] if len(cols) > 3 else "",
            "match": cols[4] if len(cols) > 4 else "",
            "applied": cols[5] if len(cols) > 5 else "",
            "url": url,
            "notes": cols[7] if len(cols) > 7 else "",
            "added": cols[8] if len(cols) > 8 else "",
            "posted": cols[9] if len(cols) > 9 else "",
        })
    return out


def existing_urls() -> set[str]:
    return {_norm_url(r["url"]) for r in rows() if r.get("url")}


def append_jobs(jobs: list[dict]) -> int:
    """Append newly-scouted jobs as `🔍 To apply` rows, skipping any URL already
    in the sheet. Never rewrites an existing row. Returns how many were added."""
    ensure_sheet()
    have = existing_urls()
    today = datetime.now().strftime("%Y-%m-%d")
    new_rows = []
    for j in jobs:
        url = (j.get("url") or "").strip()
        if not url.startswith("http") or _norm_url(url) in have:
            continue
        have.add(_norm_url(url))
        match = j.get("match_score") or j.get("match") or ""
        posted = j.get("posted") or j.get("posted_date") or j.get("date_posted") or ""
        link = f"[open]({url})"
        new_rows.append(
            f"| {DEFAULT_STATUS} | {_cell(j.get('company','?'))} | {_cell(j.get('role') or j.get('title',''))} "
            f"| {_cell(j.get('location',''))} | {_cell(match)} |  | {link} | {_cell(j.get('why',''))} | {today} | {_cell(posted)} |"
        )
    if not new_rows:
        return 0
    lines = icloud_read.read_text(SHEET).splitlines(keepends=True)
    out, inserted = [], False
    for line in lines:
        if not inserted and ABOVE in line:
            for r in new_rows:
                out.append(r + "\n")
            inserted = True
        out.append(line)
    if not inserted:                       # markers missing — append a fresh table
        out.append("\n" + BELOW + " -->\n" + _HEADER + "\n")
        out += [r + "\n" for r in new_rows]
        out.append(ABOVE + "\n")
    _write("".join(out))
    return len(new_rows)


def set_status(url: str, status: str, when: str | None = None) -> bool:
    """Change one row's Status cell (matched by URL), atomically. When set to
    Applied and the Applied date is empty, stamp `when` (default: today). Other
    lines stay byte-identical."""
    if status not in STATUSES or not SHEET.exists():
        return False
    key = _norm_url(url)
    stamp = when or datetime.now().strftime("%Y-%m-%d")
    out, in_p, changed = [], False, False
    for line in icloud_read.read_text(SHEET).splitlines(keepends=True):
        s = line.strip()
        if BELOW in s:
            in_p = True
        elif ABOVE in s:
            in_p = False
        if in_p and not changed and s.startswith("|"):
            cols = _split_cells(s)
            if len(cols) >= 7 and _norm_url(_link_url(cols[6])) == key and key:
                cols[0] = status
                if status == APPLIED_STATUS and len(cols) > 5 and not cols[5]:
                    cols[5] = stamp
                # Re-escape pipes on rejoin: _split_cells unescaped '\|' → '|', so a
                # title containing a pipe must be re-escaped or the rewritten row
                # would break the table again.
                line = ("| " + " | ".join(c.replace("|", "\\|") for c in cols)
                        + " |" + ("\n" if line.endswith("\n") else ""))
                changed = True
        out.append(line)
    if changed:
        _write("".join(out))
    return changed


def mark_applied(url: str) -> bool:
    """Convenience: flip a row to Applied (used by the fill pipeline on a verified
    submit-ready fill). Only advances 🔍 To apply → 📤 Applied; never downgrades a
    later stage Taran already set."""
    for r in rows():
        if _norm_url(r["url"]) == _norm_url(url):
            if r["status"] == DEFAULT_STATUS:
                return set_status(url, APPLIED_STATUS)
            return False
    return False
