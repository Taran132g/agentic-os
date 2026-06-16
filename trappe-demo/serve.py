#!/usr/bin/env python3
"""
Local demo server.

Serves the static demo (dashboard, analytics, voice demo) AND gives the voice
demo a real brain. POST /api/voice runs the conversation through an LLM and
returns the receptionist's next line + the booking captured so far.

Brain selection (automatic):
  • If ANTHROPIC_API_KEY is set  → Anthropic API, Haiku (sub-second). FAST.
  • else                         → `claude -p` (your Claude subscription). ~3s.

The agent also CHECKS AVAILABILITY against a live table-inventory before it
confirms — it won't book a full slot, and it offers the nearest open ones.

    python3 serve.py            # http://localhost:8755
"""

import json
import os
import re
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEMO_DIR = Path(__file__).parent
CFG = json.loads((DEMO_DIR.parent / "local_business" / "businesses" / "copper-lantern.json").read_text())
V = CFG["voice"]
MODEL = "claude-haiku-4-5"

# ── Availability (the demo's "reservation book"). In production this is a query
#    against their booking system / POS or a PAIS-hosted table store. Status per
#    slot: open | limited | full. Big parties (9+) need the party room + notice.
AVAILABILITY = {
    "Friday":   {"6:00 PM": "open", "6:30 PM": "open", "7:00 PM": "full",
                 "7:30 PM": "limited", "8:00 PM": "open", "8:30 PM": "open"},
    "Saturday": {"6:00 PM": "limited", "6:30 PM": "full", "7:00 PM": "full",
                 "7:30 PM": "full", "8:00 PM": "limited", "8:30 PM": "open"},
    "Sunday":   {"11:00 AM": "open", "12:00 PM": "limited", "1:00 PM": "open",
                 "6:00 PM": "open", "7:00 PM": "open"},
    "_default": {"6:00 PM": "open", "7:00 PM": "open", "8:00 PM": "open", "9:00 PM": "open"},
}

def availability_text():
    lines = []
    for day, slots in AVAILABILITY.items():
        label = "Mon–Thu (typical)" if day == "_default" else day
        parts = [f"{t} {'✅' if s == 'open' else ('⚠️ limited' if s == 'limited' else '❌ full')}" for t, s in slots.items()]
        lines.append(f"  {label}: " + ", ".join(parts))
    return "\n".join(lines)

SYSTEM = f"""You are the voice receptionist answering the phone for {CFG['name']}, a neighborhood tavern.
Voice: {CFG['brand_voice']}

What you know:
- {V['hours']}
- {V['highlights']}
- {V['reservations']}
- Address: {V['address']}

CURRENT AVAILABILITY (today is a Thursday; only offer/confirm slots shown open or limited):
{availability_text()}
- ✅ open = book it.  ⚠️ limited = okay but mention it's filling up.  ❌ full = do NOT book it.
- If the slot they want is full, say so warmly and offer the 1–2 NEAREST open slots that day.
- Parties of 9+ need the private party room and 48 hours' notice — offer that instead of a regular table.

Your job: take a table reservation by collecting party size, day, time, and a name —
ONE natural question at a time. Actually understand the caller; handle vague, messy,
or impossible input gracefully (e.g. a past date) — never slot nonsense. Before you
confirm, CHECK the availability above. Read the final reservation back and ask them to confirm.
{V['rules']}

Reply with ONLY a JSON object and nothing else:
{{"say": "<one or two short, natural spoken sentences>",
  "booking": {{"party": "", "day": "", "time": "", "name": ""}},
  "done": false}}
- "booking" holds everything captured SO FAR (cumulative; empty string for unknown).
  Normalize: party "6 people", day "Friday", time "7:00 PM", name "Dave".
  Only fill "time" once you've confirmed that slot is actually available.
- Set "done" to true only after the caller confirms the read-back of an available slot."""

# ── brain ──────────────────────────────────────────────────────────────────
USE_API = bool(os.environ.get("ANTHROPIC_API_KEY"))
_client = None
if USE_API:
    try:
        import anthropic
        _client = anthropic.Anthropic()
    except Exception:
        USE_API = False

def llm_raw(convo_prompt):
    if USE_API and _client:
        r = _client.messages.create(
            model=MODEL, max_tokens=500, system=SYSTEM,
            messages=[{"role": "user", "content": convo_prompt}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    full = f"{SYSTEM}\n\n{convo_prompt}"
    proc = subprocess.run(["claude", "-p", "--model", MODEL], input=full,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout

def llm_turn(messages):
    convo = "\n".join(
        f"{'CALLER' if m.get('role') == 'user' else 'RECEPTIONIST'}: {m.get('content','')}"
        for m in messages
    )
    prompt = f"Conversation so far:\n{convo}\n\nRespond with ONLY the JSON for your next turn."
    try:
        out = llm_raw(prompt)
    except Exception:
        return {"say": "Sorry, I didn't catch that — could you say it again?", "booking": {}, "done": False}
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {"say": "Sorry, could you repeat that?", "booking": {}, "done": False}
    try:
        data = json.loads(m.group(0))
        data.setdefault("booking", {}); data.setdefault("done", False)
        data.setdefault("say", "Sorry, could you say that again?")
        return data
    except Exception:
        return {"say": "Sorry, could you repeat that?", "booking": {}, "done": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DEMO_DIR), **k)

    def do_POST(self):
        if self.path != "/api/voice":
            self.send_error(404); return
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or "{}")
        except Exception:
            body = {}
        payload = json.dumps(llm_turn(body.get("messages", []))).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    brain = f"Anthropic API ({MODEL})" if USE_API else f"claude -p ({MODEL})"
    print(f"Serving {CFG['name']} demo on http://localhost:8755")
    print(f"Voice brain: {brain}  ·  availability check: on")
    ThreadingHTTPServer(("", 8755), Handler).serve_forever()
