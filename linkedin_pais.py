#!/usr/bin/env python3
"""LinkedIn PAIS outreach — finds hiring managers at actively-hiring startups
via public web research (NOT LinkedIn scraping), then drafts a copy-paste
connection note + a follow-up pitching PAIS. Queue: linkedin_pais_queue.json."""
import json, re, subprocess
from pathlib import Path
try:
    from tools.persona import persona_block
except Exception:
    def persona_block(): return ""
Q = Path(__file__).parent / "linkedin_pais_queue.json"
prompt = f"""Use WebSearch to find 3 startups or companies that are ACTIVELY HIRING right now
(recent job postings, funding news, "we're hiring" posts). For each, identify ONE likely
hiring manager, recruiter, or founder by NAME from public sources (job posting contacts,
company team pages, news). Then write LinkedIn messages FROM Taranveer Singh — a Penn State
AI Engineering student who recently built PAIS (https://pais-site.vercel.app), a platform of
AI agent workflows (inbox triage, lead scouting, outreach drafting, daily reports) run from
one Control Room.
{persona_block()}
For each person:
- CONNECT: a connection-request note UNDER 200 characters — warm, references their company's
  hiring push, no ask.
- FOLLOWUP: 3-5 lines for after they accept — mention he recently BUILT PAIS, one line on what
  it does, the link, and a soft ask for a quick chat about their hiring/automation needs.
Output ONLY a JSON array:
[{{"name":"","role":"","company":"","why":"<hiring signal found>","connect":"","followup":""}}]"""
out = subprocess.run(["claude","-p",prompt,"--allowedTools","WebSearch,WebFetch",
                      "--dangerously-skip-permissions"],capture_output=True,text=True,timeout=480).stdout
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
print(f"Found {len(people)} hiring contacts. COPY-PASTE TEMPLATES:\n")
for p in people:
    print(f"— {p.get('name')} · {p.get('role')} @ {p.get('company')} ({p.get('why','')})\n"
          f"CONNECT:\n{p.get('connect')}\n\nFOLLOW-UP (after accept):\n{p.get('followup')}\n\n---\n")
