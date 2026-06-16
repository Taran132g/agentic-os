# AI Voice Demo — "The Copper Lantern"

A fictional tavern (Trappe Tavern's profile, no real name) for demoing the AI
voice receptionist taking a live booking. Two ways to run it.

---

## Option A — In-browser, real LLM, runs on your laptop (use this tomorrow)

Start the demo with the **local server** (not plain http.server — the voice brain
needs a backend):

```bash
cd ~/agentic_os/trappe-demo
python3 serve.py            # http://localhost:8755
```

Then the **🎙 Voice demo** button on the dashboard, or `…/voice-demo.html`.

- The brain is a **real LLM** — each turn runs through `claude -p` (Haiku, your
  Claude subscription, **no API key**). It actually understands the caller; messy
  or impossible input ("yesterday at 5pm") gets handled gracefully, not slotted.
- The AI **speaks out loud**; you answer by **talking** (mic, Chrome) or **typing**.
- ~2–3s per turn (claude -p). A "thinking…" indicator covers the wait.
- **Runs on the laptop** (needs the local Python server). Vercel-static won't run
  the brain — for the iPad you'd need a serverless function; laptop is simplest.
- **Demo move:** hand them the laptop, *"call our demo restaurant and book a table"*,
  tap **Call now**, let them book a table for six on Friday and watch the AI handle
  whatever they actually say.

---

## Option B — A real phone number they can actually call

Fastest path to a number that rings an AI: a turnkey voice platform
(**Vapi**, Retell, Synthflow, or Bland). No server to host. ~15 minutes.

1. Sign up (free trial credit). Create an **Assistant**.
2. **Voice:** pick a warm US voice (e.g. ElevenLabs "Jessica" / PlayHT "Donna").
3. **Model:** any fast model (GPT-4o-mini / Claude Haiku) — latency matters on a call.
4. Paste the **System prompt** and **First message** below.
5. **Buy/attach a phone number** (≈$1–2/mo) to the assistant.
6. Call it from your phone in front of the prospect. Done.

> Self-hosted alternative (your own stack): `voice_server.py --serve` exposes the
> Twilio **ConversationRelay** TwiML + websocket; point a Twilio number's Voice
> webhook at `/voice/twiml`. Swap the dev `claude -p` brain for a realtime model
> for production latency. The persona below is the same one `voice_workflow.py`
> builds from `businesses/copper-lantern.json`.

### First message (greeting)
```
Thanks for calling The Copper Lantern! How can I help you tonight?
```

### System prompt (paste verbatim)
```
You are the voice receptionist answering the phone for The Copper Lantern, a
neighborhood tavern and kitchen. You sound like a warm, friendly, real person —
short spoken sentences, never robotic, a little playful.

What you know:
- Hours: open every day 11am–2am; kitchen until 11pm.
- Known for wings and smash burgers; Yuengling and local drafts on tap; big-screen
  sports; a private party room that seats about 30.
- You take reservations, especially for parties of 6+ and the party room.
- Address: 12 Main Street.

Your job on a call:
1. Greet, find out what they need — a table, a party/event, hours, or a question.
2. To book a table, collect — one question at a time, conversationally:
   party size → day → time → name. Then read it back and confirm.
3. Answer simple questions from what you know. If you don't know something, say
   you'll have a manager call back — never make it up.
4. Close warmly: confirm the reservation details and thank them.

Rules:
- Never take payment or credit-card information over the phone.
- Keep every reply to one or two short spoken sentences.
- Don't invent menu items you weren't told about.
- Once you have party size, day, time, and name, summarize and confirm, then end.
```

### What to say when you demo it
"Call this number." → They hear it answer, you book a table for 6 on Friday at 7,
it reads it back and confirms. Then: *"That's your phone line, answered, 24/7,
booking tables while you're slammed or closed."*
