#!/usr/bin/env python3
"""LinkedIn PAIS outreach — finds PENN STATE ALUMNI who are hiring vibe coders /
full-stack developers, via public web research (NOT LinkedIn scraping), then
drafts a copy-paste connection note + a follow-up that pitches Taranveer as the
candidate for the role. Queue: linkedin_pais_queue.json."""
import json, re, subprocess
from pathlib import Path
try:
    from tools.persona import persona_block
except Exception:
    def persona_block(): return ""
Q = Path(__file__).parent / "linkedin_pais_queue.json"
prompt = f"""Use WebSearch to find 3 PENN STATE alumni (Pennsylvania State University grads)
who are CURRENTLY HIRING a "vibe coder", full-stack developer, software engineer, or similar
build-fast generalist role. They should be founders, engineering leaders, CTOs, or hiring
managers at companies with a recent, real hiring signal (live job posting, "we're hiring" post,
funding news). For EACH person, verify TWO public facts: (1) the Penn State connection — degree,
alumni page, bio, or "Penn State" mention; and (2) the open vibe-coder / full-stack role. Use
public sources only: company careers pages, job postings, team/about pages, alumni features, news.

Then write LinkedIn messages FROM Taranveer Singh — a Penn State AI Engineering student (class of
2027) who is exactly the kind of fast, full-stack builder they're hiring. He ships end to end:
he built PAIS (https://getpais.company), a platform of autonomous AI agent workflows run from one
Control Room; BrainScan, an open-source AI product; and a live trading bot. Stack: Python, React,
AWS, Firebase, Flask/Django. He vibe-codes real, shipped products solo.
{persona_block()}
For each person:
- CONNECT: a connection-request note UNDER 200 characters — LEAD with the shared Penn State tie,
  reference the role/company they're hiring for, warm and specific, NO ask.
- FOLLOWUP: 3-5 lines for after they accept — open on the Penn State connection, position Taran
  as a candidate for their open full-stack / vibe-coder role, name 1-2 things he's shipped (PAIS,
  BrainScan) with the link, and a soft ask for a quick chat about the role. Do NOT pitch PAIS as a
  product to buy — it's proof of what he can build.
Output ONLY a JSON array (include the Penn State + hiring signal in "why"):
[{{"name":"","role":"","company":"","why":"<Penn State tie + the open role found>","connect":"","followup":""}}]"""
out = subprocess.run(["claude","-p",prompt,"--allowedTools","WebSearch,WebFetch",
                      "--dangerously-skip-permissions"],capture_output=True,text=True,timeout=900).stdout
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
    open("/tmp/li_raw.txt","w").write(out or "(empty)")
    print("PARSE FAILED — raw saved to /tmp/li_raw.txt")
try: queue = json.loads(Q.read_text())
except Exception: queue = []
seen = {(p.get("name"),p.get("company")) for p in queue}
for p in people:
    if (p.get("name"),p.get("company")) not in seen:
        p["contacted"] = False; queue.append(p)
Q.write_text(json.dumps(queue, indent=2))
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
