"""linkedin_sheet.py — the single source of truth for Taran's LinkedIn outreach.

Same pattern as tools/job_sheet.py: an editable markdown table in the Obsidian
vault that the LinkedIn agent APPENDS targets to, the Control Room reads + edits
status on, and Taran can hand-edit directly. The agent never rewrites a row he
has touched. ToS-safe — PAIS only drafts; Taran sends invites by hand.

Status flow:  🔍 To send → 📨 Invite sent → 🤝 Connected → 💬 Replied
Terminal:     ⚪ Skip   (drops off the active sheet, stays in the note)

Row key = (name, company) — LinkedIn targets have no stable public URL, and a
name alone can repeat across companies.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from tools import icloud_read

VAULT = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
         "Documents" / "Digital Brain")
SHEET = VAULT / "Projects & Building" / "LinkedIn Pipeline.md"

BELOW = "<!-- LI_AGENT_APPEND_BELOW"          # substring match
ABOVE = "<!-- LI_AGENT_APPEND_ABOVE -->"

STATUSES = ["🔍 To send", "📨 Invite sent", "🤝 Connected", "💬 Replied", "⚪ Skip"]
DEFAULT_STATUS = STATUSES[0]
SENT_STATUS = STATUSES[1]

_HEADER = (
    "| Status | Name | Role | Company | Why | Sent | Connect note | Added |\n"
    "|--------|------|------|---------|-----|------|--------------|-------|"
)

_SCAFFOLD = f"""---
type: pipeline
updated: {{today}}
---

# LinkedIn Pipeline

Your live networking tracker. The **LinkedIn agent** scouts targets and drafts a
connect note for each (appended below as `🔍 To send`). Change a row's status in
the Control Room (or here) and it saves straight to this note. PAIS never sends —
copy the connect note, send the invite by hand, then mark it `📨 Invite sent`.
Rows you mark `⚪ Skip` stay in the note but drop off the active sheet.

| Code | Meaning |
|------|---------|
| 🔍 To send | Drafted, invite not yet sent |
| 📨 Invite sent | Connection request sent |
| 🤝 Connected | They accepted |
| 💬 Replied | In conversation |
| ⚪ Skip | Not pursuing |

{BELOW} — the agent inserts new targets between these markers; safe to edit any cell -->
{_HEADER}
{ABOVE}
"""


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _cell(v: str) -> str:
    """Escape pipes/newlines so a value can't break the markdown table."""
    return str(v or "").replace("|", "\\|").replace("\n", " ").strip()


def _split_cells(line: str) -> list[str]:
    """Split a markdown table row into cells on UNESCAPED pipes, then unescape.

    _cell() writes a literal pipe as '\\|', so a naive split('|') mis-columns any
    row whose name/company/note contains a pipe — shifting key columns so the row
    can't be matched or status-edited from the Control Room ('Couldn't save')."""
    parts = re.split(r"(?<!\\)\|", line.strip())
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def ensure_sheet() -> None:
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
        if len(cols) < 2 or cols[1].lower() == "name" or re.fullmatch(r"-+", cols[1] or ""):
            continue
        out.append({
            "status": cols[0],
            "name": cols[1],
            "role": cols[2] if len(cols) > 2 else "",
            "company": cols[3] if len(cols) > 3 else "",
            "why": cols[4] if len(cols) > 4 else "",
            "sent": cols[5] if len(cols) > 5 else "",
            "connect": cols[6] if len(cols) > 6 else "",
            "added": cols[7] if len(cols) > 7 else "",
        })
    return out


def _existing_keys() -> set[tuple]:
    return {(_norm(r["name"]), _norm(r["company"])) for r in rows()}


def append_people(people: list[dict]) -> int:
    """Append new targets as `🔍 To send` rows, skipping any (name, company) already
    in the sheet. Never rewrites an existing row. Returns how many were added."""
    ensure_sheet()
    existing = rows()
    have = {(_norm(r["name"]), _norm(r["company"])) for r in existing}
    # Also dedup on name alone: the scout reports the same person under company
    # variants ("Emergent" vs "Emergent Labs"), which the (name, company) key
    # would let through as a duplicate row.
    have_names = {_norm(r["name"]) for r in existing}
    today = datetime.now().strftime("%Y-%m-%d")
    new_rows = []
    for p in people:
        name, company = (p.get("name") or "").strip(), (p.get("company") or "").strip()
        if not name or (_norm(name), _norm(company)) in have or _norm(name) in have_names:
            continue
        have.add((_norm(name), _norm(company)))
        have_names.add(_norm(name))
        new_rows.append(
            f"| {DEFAULT_STATUS} | {_cell(name)} | {_cell(p.get('role',''))} | {_cell(company)} "
            f"| {_cell(p.get('why',''))} |  | {_cell(p.get('connect',''))} | {today} |"
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
    if not inserted:
        out.append("\n" + BELOW + " -->\n" + _HEADER + "\n")
        out += [r + "\n" for r in new_rows]
        out.append(ABOVE + "\n")
    _write("".join(out))
    return len(new_rows)


def set_status(name: str, company: str, status: str, when: str | None = None) -> bool:
    """Change one row's Status cell (matched by name + company), atomically. When set
    to Invite sent and the Sent date is empty, stamp `when` (default: today)."""
    if status not in STATUSES or not SHEET.exists():
        return False
    kn, kc = _norm(name), _norm(company)
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
            if len(cols) >= 4 and _norm(cols[1]) == kn and _norm(cols[3]) == kc and kn:
                cols[0] = status
                if status == SENT_STATUS and len(cols) > 5 and not cols[5]:
                    cols[5] = stamp
                # Re-escape pipes on rejoin (a field may legitimately contain one).
                line = ("| " + " | ".join(c.replace("|", "\\|") for c in cols)
                        + " |" + ("\n" if line.endswith("\n") else ""))
                changed = True
        out.append(line)
    if changed:
        _write("".join(out))
    return changed
