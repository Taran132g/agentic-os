#!/usr/bin/env python3
"""sales_agent.py — refill the Piontrix cold-call pipeline (2026-06-16).

The Control Room "Sales" agent. Unlike piontrix_scout.py (which queues leads for
EMAIL outreach of generic web/dashboard work), this scouts businesses for the
**local-business workflow offer** — Reactivation, Missed-call/Voice, Reviews —
and appends them to the editable vault call sheet so Taran can WALK IN or CALL.

It applies the screening filter (skip anyone already running a tool that does the
job; prioritize visible leaks), dedupes against every business already on the
sheet, and inserts new rows as "🟣 To call" directly above the append marker.
It NEVER rewrites a row Taran has already edited — only inserts new ones.

Run:
    python3 sales_agent.py            # find + append new cold-call prospects
Env:
    SALES_AGENT_DRY=1   print finds, don't write the sheet
    SALES_AGENT_N=6     how many to find per run (default 6)
Exit 0 always (so the morning stack chain never aborts on a bad run).
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from shutil import which

import requests
from dotenv import load_dotenv

AGENTIC_DIR = Path(__file__).resolve().parent
load_dotenv(AGENTIC_DIR / ".env")

# The living call sheet in the Obsidian vault (source of truth for the pipeline).
VAULT = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" /
         "Documents" / "Digital Brain")
SHEET = VAULT / "Projects & Building" / "Piontrix Sales Pipeline.md"
APPEND_MARKER = "<!-- SALES_AGENT_APPEND_ABOVE -->"
APPEND_START = "<!-- SALES_AGENT_APPEND_BELOW"

AREA = ("Royersford, Spring City, Phoenixville, Collegeville, Trappe, Audubon, "
        "Oaks, Eagleville, Norristown, Bridgeport, and King of Prussia PA "
        "(the Route 422 corridor, within ~15 miles of Royersford 19468)")


def _tg(text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and cid):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": int(cid), "text": text[:4000],
                            "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=20)
    except Exception:
        pass


def _norm_phone(s: str) -> str:
    """Last 10 digits of a phone number — the dedupe key. '' if no real number."""
    digits = re.sub(r"\D", "", str(s or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def _read_sheet() -> str:
    """Read the call sheet, tolerating an iCloud-evicted (dataless) file.

    The vault lives in iCloud; the note can be a placeholder mid-sync, and the
    on-demand download can momentarily fail (OSError) instead of blocking. That
    is exactly what crashed the 06-23 scheduled run. Nudge a download and retry
    a few times; if it never materializes, raise a clear, identifiable error so
    the caller can fail soft rather than dump a traceback into the feed."""
    last: OSError | None = None
    for attempt in range(4):
        try:
            return SHEET.read_text(encoding="utf-8")
        except OSError as e:
            last = e
            # Touch the path to ask iCloud to materialize it, then back off.
            try:
                SHEET.stat()
                subprocess.run(["brctl", "download", str(SHEET)], capture_output=True, timeout=20)
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"call sheet unreadable (iCloud not materialized): {last}")


def _existing_index() -> tuple[set[str], set[str]]:
    """Every business already on the sheet, as (names, phone-keys) — for dedupe.
    This is the agent's persistent memory: anything on the sheet (ANY status,
    including Rejected/Skip) is excluded from future runs."""
    names: set[str] = set()
    phones: set[str] = set()
    if not SHEET.exists():
        return names, phones
    in_pipeline = False
    for line in _read_sheet().splitlines():
        s = line.strip()
        if APPEND_START in s:
            in_pipeline = True
            continue
        if APPEND_MARKER in s:
            break
        if not (in_pipeline and s.startswith("|")):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        # Pipeline rows: | Status | Business | Vertical | Phone | ... — skip header/separator.
        if len(cols) >= 2 and cols[1] and cols[1].lower() != "business" and not re.fullmatch(r"-+", cols[1]):
            names.add(cols[1].lower())
            if len(cols) >= 4:
                ph = _norm_phone(cols[3])
                if ph:
                    phones.add(ph)
    return names, phones


class SessionLimit(RuntimeError):
    """The claude CLI returned a usage/session-limit notice instead of output.

    This is NOT a code fault — the morning stack runs several claude agents back
    to back and sales, near the end of the order, can exhaust the subscription's
    rolling session budget. The run should fail SOFT (deferred, not broken) so it
    isn't mistaken for a crash. Carries the reset hint when the CLI provides one."""

    def __init__(self, reset: str = ""):
        self.reset = reset
        super().__init__(f"claude session limit{f' · resets {reset}' if reset else ''}")


# The CLI prints a one-line notice like "You've hit your session limit · resets
# 9:40am (America/New_York)" — prose, exit 0, no JSON. Match it before we go
# hunting for a '[' so the failure is identified, not opaque ("no JSON array").
_LIMIT_RE = re.compile(r"\b(session|usage|rate)\s+limit\b|\blimit\s+reached\b", re.I)
_RESET_RE = re.compile(r"resets?\s+(.+?)\s*$", re.I)


def _research(n: int, exclude_names: list[str] | None = None) -> list[dict]:
    if not which("claude"):
        raise RuntimeError("claude CLI not on PATH")
    exclude_block = ""
    if exclude_names:
        listed = "; ".join(sorted(exclude_names)[:120])
        exclude_block = (
            f"\nALREADY ON THE CALL SHEET — DO NOT return any of these (find DIFFERENT "
            f"businesses, and avoid obvious re-spellings of them):\n{listed}\n")
    prompt = f"""You are a lead scout for a one-person agency that sells DONE-FOR-YOU
AI WORKFLOWS to local small businesses. The offer (lead with the leak the owner
FEELS, never "AI"):
  • Reactivation — text lapsed regulars / overdue clients back, from the owner's number
  • Missed-call / AI Voice receptionist — catch booking calls that ring out
  • Reviews — grow a thin review count and reply automatically

Use WebSearch (and WebFetch to verify) to find {n} REAL, independent, owner-operated
local businesses in this area: {AREA}.
{exclude_block}
SCREENING FILTER — SKIP a business if it already runs a tool that does my job:
  • Toast / Square online ordering with bundled loyalty/marketing
  • Review/messaging suites: Podium, Birdeye, Weave, Solutionreach, Demandforce
  • Booking+marketing platforms: Mindbody, Boulevard, Vagaro, fitDEGREE, Jane,
    OpenTable/Resy WITH reminders
  • Chains / franchises / DSO-owned (no local decision-maker)
  • Big enough for in-house marketing/IT; or no reachable owner / dead online presence

PRIORITIZE businesses with a VISIBLE leak my workflows plug:
  • Good rating but LOW review count (4.5+, under ~50 reviews)
  • "Call to book" / reservations by phone, no booking widget
  • Reviews complaining "couldn't get through / no one answered"
  • Doesn't reply to Google reviews; slow review velocity
  • Basic / Facebook-only / old website; appointment- or reservation-driven;
    owner-operated single location; newer (<2 yrs); active-but-manual (texts by hand)

Good verticals: restaurants/bars/cafes, salons/spas/barbershops, boutique
gyms/studios, chiropractors/dentists/med-spas, auto repair, pet grooming,
contractors. EXCLUDE chains and national brands.

For EACH business, find a real PHONE number (check website/Google listing).

Output ONLY a JSON array of {n} objects, no prose, no code fences:
[{{"business":"","vertical":"<short, e.g. 'Hair salon'>","phone":"<number or 'verify on Google'>",
"location":"<town, PA>","best_window":"<good time to reach the owner, e.g. '2:00–4:00pm'>",
"lead_workflow":"<which of Reactivation / Missed-call / Voice / Reviews to lead with>",
"hook":"<one sentence naming the leak the owner feels and the rough dollar on it>",
"verify":"<what to double-check, e.g. 'confirm no Vagaro' — or '' if clean>"}}]"""
    cmd = ["claude", "-p", prompt,
           "--allowedTools", "WebSearch,WebFetch",
           "--dangerously-skip-permissions"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    raw = re.sub(r"```(?:json)?|```", "", (res.stdout or "").strip())
    start = raw.find("[")
    if start == -1:
        # Distinguish a transient session/usage limit (fail soft) from a real
        # malformed response (fail hard) — they were conflated as "no JSON array".
        if _LIMIT_RE.search(raw):
            m = _RESET_RE.search(raw)
            raise SessionLimit(m.group(1).strip() if m else "")
        raise RuntimeError(f"no JSON array from claude: {raw[:200]}")
    # raw_decode parses just the first valid array and ignores any trailing
    # prose or a duplicate array claude sometimes appends after it.
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bad JSON from claude ({e}): {raw[start:start + 200]}")
    return data if isinstance(data, list) else []


def _cell(v: str) -> str:
    """Sanitize a value for a markdown table cell (no pipes / newlines)."""
    return str(v or "").replace("|", "/").replace("\n", " ").strip()


def _row(lead: dict) -> str:
    notes = _cell(lead.get("hook", ""))
    verify = _cell(lead.get("verify", ""))
    if verify:
        notes = f"{notes} ⚠ {verify}".strip()
    return (f"| 🟣 To call | {_cell(lead.get('business'))} | {_cell(lead.get('vertical'))} "
            f"| {_cell(lead.get('phone')) or 'verify on Google'} | {_cell(lead.get('best_window')) or '—'} "
            f"| {_cell(lead.get('lead_workflow')) or 'Reactivation + Missed-call'} | — | {notes} |")


def _append_rows(rows: list[str]) -> None:
    """Insert new table rows directly above the append marker, atomically.
    Existing rows (and Taran's edits to them) are left untouched."""
    text = SHEET.read_text(encoding="utf-8")
    if APPEND_MARKER not in text:
        # Marker missing — append at end rather than lose the data.
        text = text.rstrip() + "\n\n" + APPEND_MARKER + "\n"
    block = "\n".join(rows) + "\n"
    text = text.replace(APPEND_MARKER, block + APPEND_MARKER, 1)
    tmp = SHEET.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, SHEET)


def main() -> int:
    n = int(os.environ.get("SALES_AGENT_N", "6"))
    dry = os.environ.get("SALES_AGENT_DRY") == "1"

    try:
        seen_names, seen_phones = _existing_index()
        found = _research(n, exclude_names=list(seen_names))
    except SessionLimit as e:
        # Healthy agent, exhausted budget — defer, don't cry failure. Keeping this
        # distinct stops the reviewer from grading a working agent as "broken".
        reset = f" (resets {e.reset})" if e.reset else ""
        msg = f"⏳ Sales agent deferred — claude session limit{reset}; no new prospects this run."
        print(msg)
        if not dry:
            _tg(f"⏳ <b>Sales agent</b> deferred — claude session limit{reset}. "
                f"No code fault; it'll refill the call sheet on the next run.")
        return 0
    except Exception as e:
        msg = f"⚠️ Sales agent scout failed: {e}"
        print(msg)
        if not dry:
            _tg(f"⚠️ <b>Sales agent</b> failed: {e}")
        return 0

    new = []
    for f in found:
        biz = str(f.get("business", "")).strip()
        if not biz or biz.lower() in seen_names:
            continue
        ph = _norm_phone(f.get("phone", ""))
        if ph and ph in seen_phones:
            continue  # same number already on the sheet — catches name re-spellings
        seen_names.add(biz.lower())
        if ph:
            seen_phones.add(ph)
        new.append(f)

    print(f"Sales agent — searched the Royersford↔KOP corridor.")
    print(f"Found {len(found)} candidates, {len(new)} new after dedupe against the call sheet.\n")
    for l in new:
        print(f"• {l.get('business')} ({l.get('location','')}) — {l.get('vertical','')}")
        print(f"    ☎ {l.get('phone','verify')} · best {l.get('best_window','?')} · lead: {l.get('lead_workflow','')}")
        print(f"    leak: {l.get('hook','')}")
        if l.get("verify"):
            print(f"    ⚠ verify: {l.get('verify')}")

    if dry:
        print("\n[dry run — sheet not written]")
        return 0

    if not new:
        msg = "🔎 Sales agent — no NEW qualified prospects this run (all finds already on the call sheet)."
        print(msg)
        _tg("🔎 <b>Sales agent</b> — searched Royersford↔KOP, no NEW prospects (all already on the sheet).")
        return 0

    _append_rows([_row(l) for l in new])
    print(f"\nAppended {len(new)} new prospect(s) to the call sheet as '🟣 To call'.")
    print(f"Sheet: {SHEET}")

    lines = [f"🔎 <b>Sales agent — {len(new)} new cold-call prospects added</b>",
             "<i>Royersford ↔ KOP · now on your call sheet as 🟣 To call</i>", ""]
    for l in new:
        lines.append(f"• <b>{l.get('business')}</b> ({l.get('location','')}) — {l.get('vertical','')}")
        lines.append(f"  ☎ {l.get('phone','verify')} · lead {l.get('lead_workflow','')}")
        lines.append(f"  <i>{l.get('hook','')}</i>")
    _tg("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"SALES_AGENT FAILED: {e}", file=sys.stderr)
        sys.exit(1)
