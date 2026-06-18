# ElevenLabs Voice Host — setup & demo

The voice receptionist's brain is now an **ElevenLabs restaurant-host agent**:
ElevenLabs owns the voice, speech-to-text, the conversational LLM, and the
low-latency turn-taking. Our code is the **booking backend** — the agent calls
two tools (`check_availability`, `book_table`) and the reservation lands in the
same owner-approval queue the old flow used (`hand_to_owner`).

Old `claude -p` per-turn brain (`serve.py /api/voice`, `voice_workflow.ClaudeCLIBrain`)
is kept only as an offline fallback. The live demo uses the ElevenLabs agent.

---

## One-time: create the agent (your ElevenLabs account)

The key is read automatically from the vault `API Keys.md` (or `ELEVENLABS_API_KEY`).
ElevenLabs **Agents** requires a paid plan that includes Conversational AI minutes.

### Path A — provision via API (fastest)
```bash
cd ~/agentic_os
python3 -m local_business.elevenlabs_voice --business copper-lantern --provision
# prints: agent_id: <id>   (saved to local_business/.state/elevenlabs_agents.json)
```

### Path B — dashboard (most reliable if the API schema drifted)
```bash
python3 -m local_business.elevenlabs_voice --business copper-lantern --setup    # the steps
python3 -m local_business.elevenlabs_voice --business copper-lantern --prompt   # system prompt
python3 -m local_business.elevenlabs_voice --business copper-lantern --config   # tool params
```
In **ElevenLabs → Agents → Create agent**: paste the prompt + first message, pick a
warm host voice, then add two **client** tools named exactly `check_availability`
and `book_table` (params from `--config`). Copy the agent_id and either:
```bash
export ELEVENLABS_AGENT_ID=<id>          # or add it to elevenlabs_agents.json
```

Pin a specific voice/model per restaurant in `businesses/<id>.json`:
```json
"voice": { "elevenlabs_voice_id": "<id>", "elevenlabs_llm": "gemini-2.0-flash", ... }
```

---

## Run the browser demo
```bash
cd ~/agentic_os/trappe-demo
python3 serve.py            # http://localhost:8755 → 🎙 Voice demo
```
Tap **Call now**, allow the mic, and talk. The host checks the table (won't book a
full slot, offers nearest open times, sends 9+ to the party room) and books it —
the booking card + call history update and the reservation POSTs to the owner
queue. If you see "agent not provisioned yet", do the one-time setup above.

Verify the booking reached the owner queue:
```bash
python3 -c "from local_business.state import RuntimeState as R; print(R.load_or_new('copper-lantern').pending_count)"
```

---

## Later: a real phone number (same agent)
In ElevenLabs, attach a phone number (native or Twilio) to the agent. Switch the
two tools from **client** to **server** tools pointing at public webhooks, and add
a **post-call webhook** → `elevenlabs_voice.handle_post_call(business, state, payload)`
(host it on Oracle `pais-api`). Tool names/params are unchanged, so the demo agent
promotes to a phone agent without a rewrite.

## Cost note
ElevenLabs Agents bill **per minute** (~$0.10–0.50/min by plan) — real COGS, unlike
the `claude -p` path. Price the voice tier cost-plus on minutes, not flat.

## Offline check (no key, no network, no tokens)
```bash
python3 -m local_business.elevenlabs_voice --business copper-lantern --selftest
```
