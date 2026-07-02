"""One-off: add an `Added` column to the three pipeline notes.

Header gets 'Added', separator gets dashes, existing data rows get '—' (they
predate per-row timestamping — honest 'unknown' rather than a fake date). New
rows appended by the agents from now on carry a real YYYY-MM-DD.

Modes:
  (default)  dry run — report how many rows WOULD change
  --apply    add the Added column (idempotent; skips already-migrated notes)
  --reverse  undo a prior BROKEN apply that merged 'Added' into the last cell
             (suffixes ' Added |' / ' ------- |' / ' — |'); restores the closing
             pipe so the note is back to its pre-migration shape

Atomic tmp+replace throughout.
"""
import os
import sys
from pathlib import Path

VAULT = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
         "Documents" / "Digital Brain" / "Projects & Building")

NOTES = [
    ("Job Pipeline.md", "JOB_AGENT_APPEND_BELOW", "JOB_AGENT_APPEND_ABOVE"),
    ("LinkedIn Pipeline.md", "LI_AGENT_APPEND_BELOW", "LI_AGENT_APPEND_ABOVE"),
    ("Piontrix Sales Pipeline.md", "SALES_AGENT_APPEND_BELOW", "SALES_AGENT_APPEND_ABOVE"),
]

BACKFILL = "—"
HEADER_SFX = " Added |"
SEP_SFX = " ------- |"
DATA_SFX = f" {BACKFILL} |"


def _is_sep(s: str) -> bool:
    return bool(s) and set(s) <= {"-", "|", ":", " "}


def _is_header(s: str) -> bool:
    return "Status" in s and ("Company" in s or "Name" in s or "Business" in s)


def _add_cell(line: str, val: str) -> str:
    """Append a new cell AFTER the row's closing pipe: '| a | b |' -> '| a | b | val |'."""
    nl = "\n" if line.endswith("\n") else ""
    s = line.rstrip("\n").rstrip()
    if not s.endswith("|"):
        return line
    return s + f" {val} |" + nl


def _strip_suffix(line: str, sfx: str) -> str:
    """Undo a broken append: drop the injected ' <val> |' and restore the pipe."""
    nl = "\n" if line.endswith("\n") else ""
    s = line.rstrip("\n").rstrip()
    if not s.endswith(sfx):
        return line
    return s[: -len(sfx)] + "|" + nl


def _walk(path: Path, below: str, above: str, fn) -> int:
    text = path.read_text(encoding="utf-8")
    out, in_p, changed = [], False, 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if below in s:
            in_p = True; out.append(line); continue
        if above in s:
            in_p = False; out.append(line); continue
        if in_p and s.startswith("|"):
            new = fn(line, s)
            if new != line:
                changed += 1
            out.append(new)
        else:
            out.append(line)
    if changed:
        tmp = str(path) + ".tmp"
        Path(tmp).write_text("".join(out), encoding="utf-8")
        os.replace(tmp, str(path))
    return changed


def apply_fn(line: str, s: str) -> str:
    # already migrated? header last cell is a standalone 'Added'
    if _is_header(s) and s.rstrip().rstrip("|").rstrip().endswith("| Added"):
        return line
    if _is_header(s):
        return _add_cell(line, "Added")
    if _is_sep(s):
        return _add_cell(line, "-------")
    return _add_cell(line, BACKFILL)


def reverse_fn(line: str, s: str) -> str:
    if _is_header(s):
        return _strip_suffix(line, HEADER_SFX)
    if _is_sep(s):
        return _strip_suffix(line, SEP_SFX)
    return _strip_suffix(line, DATA_SFX)


if __name__ == "__main__":
    if "--reverse" in sys.argv:
        fn, label = reverse_fn, "REVERSED"
    elif "--apply" in sys.argv:
        fn, label = apply_fn, "APPLIED"
    else:
        fn, label = apply_fn, "(dry: run with --apply)"
    dry = fn is apply_fn and "--apply" not in sys.argv
    for name, below, above in NOTES:
        p = VAULT / name
        if not p.exists():
            print(f"  MISSING  {name}"); continue
        if dry:
            # count without writing
            text = p.read_text(encoding="utf-8"); in_p = False; n = 0
            for line in text.splitlines(keepends=True):
                st = line.strip()
                if below in st: in_p = True; continue
                if above in st: in_p = False; continue
                if in_p and st.startswith("|") and apply_fn(line, st) != line: n += 1
            print(f"  {name}: would change {n} rows")
        else:
            print(f"  {name}: {label} {_walk(p, below, above, fn)} rows")
