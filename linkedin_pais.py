#!/usr/bin/env python3
"""LinkedIn PAIS outreach — finds people who are HIRING for a Summer 2027 SWE
INTERNSHIP or a SWE CONTRACT / freelance role, via public web research (NOT
LinkedIn scraping), then drafts a copy-paste connection note + a follow-up that
pitches Taranveer as the candidate. Prefers Penn State alumni (warmest tie) but
includes strong non-PSU matches; location-agnostic. Queue: linkedin_pais_queue.json."""
import json, re, subprocess
from pathlib import Path
try:
    from tools.persona import persona_block
except Exception:
    def persona_block(): return ""
Q = Path(__file__).parent / "linkedin_pais_queue.json"
prompt = f"""Use WebSearch to find 3 people who are CURRENTLY HIRING for either
(a) a Software Engineering INTERNSHIP for Summer 2027, or (b) a SOFTWARE ENGINEERING
CONTRACT / freelance / part-time build role. They should be founders, engineering leaders,
CTOs, recruiters, or hiring managers at companies with a recent, REAL hiring signal — a live
2027-intern or contract SWE posting, a "we're hiring" post, or funding news. Location does NOT
matter (remote is fine).

PREFER Penn State (Pennsylvania State University) alumni — a shared-school tie is Taran's warmest
intro — but include strong non-PSU matches too when the hiring signal is real.

For EACH person, verify the hiring signal from a PUBLIC source (careers page, job posting,
team/about page, "we're hiring" post, funding news). If the person is a Penn State alum, ALSO
verify that tie (degree, alumni page, bio, or "Penn State" mention). Public sources only — NO
LinkedIn scraping. Do NOT invent a posting: if you cannot cite a specific live 2027-intern or
SWE-contract opening, drop that person rather than padding the list to 3.

Then write LinkedIn messages FROM Taranveer Singh — a Penn State AI Engineering student (class of
2027) and a fast, full-stack builder looking for a Summer 2027 SWE internship and open to SWE
contract work now. He ships end to end: he built PAIS (https://getpais.company), a platform of
autonomous AI agent workflows run from one Control Room; BrainScan, an open-source AI product; and
a live trading bot. Stack: Python, React, AWS, Firebase, Flask/Django.
{persona_block()}
For each person:
- CONNECT: a connection-request note UNDER 200 characters. If they're a Penn State alum, LEAD with
  the shared Penn State tie; otherwise LEAD with the specific role/company they're hiring for.
  Warm and specific, NO ask.
- FOLLOWUP: 3-5 lines for after they accept — open on the connection (Penn State tie if any, else
  their work/role), position Taran as a candidate for their open 2027 SWE internship or SWE
  contract role, name 1-2 things he's shipped (PAIS, BrainScan) with the link, and a soft ask for
  a quick chat about the role. Do NOT pitch PAIS as a product to buy — it's proof of what he builds.
Output ONLY a JSON array (put the hiring signal — and the Penn State tie, if any — in "why"):
[{{"name":"","role":"","company":"","why":"<live 2027-intern or SWE-contract signal + PSU tie if any>","connect":"","followup":""}}]"""
import sys

# ── Run the research agent. Handle the CLI failing/hanging instead of letting
#    an uncaught exception (or a rate-limit banner) masquerade as "0 results". ──
try:
    proc = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "WebSearch,WebFetch",
         "--dangerously-skip-permissions"],
        capture_output=True, text=True, timeout=900,
    )
except FileNotFoundError:
    sys.exit("ERROR: `claude` CLI not found on PATH — cannot run LinkedIn agent.")
except subprocess.TimeoutExpired:
    sys.exit("ERROR: LinkedIn research timed out after 15 min. Nothing written; re-run later.")

out = proc.stdout or ""
# Detect Claude usage/session-limit banners (arrive on stdout with exit 0) so a
# rate-limited run reports clearly instead of "PARSE FAILED / Found 0".
_low = out.lower()
if any(s in _low for s in ("session limit", "usage limit", "hit your limit", "rate limit")):
    open("/tmp/li_raw.txt", "w").write(out)
    sys.exit(f"RATE LIMITED — Claude usage cap hit; queue untouched. Re-run after reset.\n{out.strip()[:200]}")
if proc.returncode != 0 and not out.strip():
    open("/tmp/li_raw.txt", "w").write((out + "\n--- STDERR ---\n" + (proc.stderr or "")))
    sys.exit(f"ERROR: claude exited {proc.returncode} with no output. See /tmp/li_raw.txt.")

cleaned = re.sub(r"```(?:json)?", "", out)
dec = json.JSONDecoder()
people = []
for i, ch in enumerate(cleaned):
    if ch == "[":
        try:
            val, _ = dec.raw_decode(cleaned[i:])
            if isinstance(val, list) and val and isinstance(val[0], dict) and "name" in val[0]:
                people = val
                break
        except Exception:
            continue
if not people:
    open("/tmp/li_raw.txt", "w").write(out or "(empty)")
    sys.exit("PARSE FAILED — no JSON array found. Raw output saved to /tmp/li_raw.txt. Queue untouched.")

# Read the existing queue. On a read error, BAIL — do NOT fall back to [] and
# overwrite the file, or one transient read wipes every saved contact.
try:
    queue = json.loads(Q.read_text())
    if not isinstance(queue, list):
        queue = []
except FileNotFoundError:
    queue = []
except Exception as e:
    open("/tmp/li_raw.txt", "w").write(out)
    sys.exit(f"ERROR reading {Q.name}: {e}. Refusing to overwrite it. Draft saved to /tmp/li_raw.txt.")

# Dedup on (name, company) AND on name alone — the LLM reports the same person
# under company-name variants ("Emergent" vs "Emergent Labs"), which a strict
# (name, company) key lets slip through as a duplicate row.
def _n(s): return re.sub(r"\s+", " ", (s or "").strip().lower())
seen_pairs = {(_n(p.get("name")), _n(p.get("company"))) for p in queue}
seen_names = {_n(p.get("name")) for p in queue}
added = 0
for p in people:
    key = (_n(p.get("name")), _n(p.get("company")))
    if key in seen_pairs or _n(p.get("name")) in seen_names:
        continue
    seen_pairs.add(key); seen_names.add(_n(p.get("name")))
    p["contacted"] = False; queue.append(p); added += 1

# Atomic write so a crash mid-write can't truncate the queue.
_tmp = Q.with_suffix(".json.tmp")
_tmp.write_text(json.dumps(queue, indent=2))
_tmp.replace(Q)
print(f"(queue: +{added} new of {len(people)} researched)")
# Mirror new targets into the editable vault LinkedIn Pipeline (source of truth
# for the Control Room tracker). Best-effort — never break the run on a sheet error.
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from tools import linkedin_sheet as _ls
    _added = _ls.append_people(people)
    print(f"(LinkedIn Pipeline: +{_added} new row(s))")
except Exception as _e:
    print(f"(LinkedIn Pipeline append skipped: {str(_e)[:120]})")
print(f"Found {len(people)} hiring contacts. COPY-PASTE TEMPLATES:\n")
for p in people:
    print(f"— {p.get('name')} · {p.get('role')} @ {p.get('company')} ({p.get('why','')})\n"
          f"CONNECT:\n{p.get('connect')}\n\nFOLLOW-UP (after accept):\n{p.get('followup')}\n\n---\n")
