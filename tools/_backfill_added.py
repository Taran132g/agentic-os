"""One-off backfill: set the `Added` cell from a defensible proxy date.

  Jobs:     Added <- Applied date   (any row whose Applied cell is a YYYY-MM-DD)
  LinkedIn: Added <- Sent date      (any row whose Sent cell is a YYYY-MM-DD)

Only touches rows whose Added is still the '—' backfill (never clobbers a real
date). Rewrites just the changed rows, re-escaping pipes so titles containing
'|' can't break the table. Atomic tmp+replace. Run with --apply to write.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import job_sheet, linkedin_sheet

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNKNOWN = {"—", ""}


def _rejoin(cols: list[str], nl: str) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in cols) + " |" + nl


def backfill(mod, below: str, above: str, src_idx: int, add_idx: int, apply: bool) -> list:
    path = mod.SHEET
    changed = []
    out, in_p = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        s = line.strip()
        if below in s:
            in_p = True; out.append(line); continue
        if above in s:
            in_p = False; out.append(line); continue
        if in_p and s.startswith("|"):
            cols = mod._split_cells(s)
            # skip header/separator
            is_hdr = len(cols) < 2 or cols[1].lower() in ("company", "name") or re.fullmatch(r"-+", cols[1] or "")
            if not is_hdr and len(cols) > max(src_idx, add_idx):
                src = cols[src_idx].strip()
                if DATE.match(src) and cols[add_idx].strip() in UNKNOWN:
                    label = cols[1]
                    cols[add_idx] = src
                    nl = "\n" if line.endswith("\n") else ""
                    line = _rejoin(cols, nl)
                    changed.append((label, src))
        out.append(line)
    if apply and changed:
        tmp = str(path) + ".tmp"
        Path(tmp).write_text("".join(out), encoding="utf-8")
        os.replace(tmp, str(path))
    return changed


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    # jobs: Applied=col5 -> Added=col8 ; linkedin: Sent=col5 -> Added=col7
    for tag, mod, below, above, src, add in [
        ("JOBS", job_sheet, "JOB_AGENT_APPEND_BELOW", "JOB_AGENT_APPEND_ABOVE", 5, 8),
        ("LINKEDIN", linkedin_sheet, "LI_AGENT_APPEND_BELOW", "LI_AGENT_APPEND_ABOVE", 5, 7),
    ]:
        ch = backfill(mod, below, above, src, add, apply)
        print(f"== {tag}: {'SET' if apply else 'would set'} {len(ch)} rows ==")
        for label, d in ch:
            print(f"   {d}  {label}")
