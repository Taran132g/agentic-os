"""
Voice receptionist transport.

Runnable today without a phone number or any paid key:
    python -m local_business.voice_server --business trappe-tavern --selftest
        scripted call end-to-end (no LLM, no tokens) → writes a booking to state
    python -m local_business.voice_server --business trappe-tavern --repl
        talk to the receptionist in your terminal (uses claude -p brain)

Production (real phone line) — Twilio ConversationRelay:
    Twilio answers the call, does STT + TTS, and opens a websocket to us; we
    only send back the words. Point a Twilio number's Voice webhook at
    GET /voice/twiml (returns the <ConversationRelay> TwiML below), and Twilio
    streams the conversation to /voice/ws. The FastAPI app at the bottom serves
    both when `fastapi`/`uvicorn` are installed; the REPL/selftest paths need
    neither, so the loop is verifiable in any environment.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import business as biz
from .state import RuntimeState
from .voice_workflow import VoiceCall, ClaudeCLIBrain, ScriptedBrain, hand_to_owner

log = logging.getLogger("voice")

# Twilio ConversationRelay TwiML. `wss_url` is this server's public ws endpoint.
TWIML = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <ConversationRelay url="{wss_url}" welcomeGreeting="{greeting}"
                       voice="en-US-Neural2-D" ttsProvider="Google" />
  </Connect>
</Response>"""


def twiml(business, wss_url: str) -> str:
    greeting = f"Thanks for calling {business.name}! How can I help you?"
    return TWIML.format(wss_url=wss_url, greeting=greeting)


async def run_selftest(business) -> None:
    """Drive a full call with a scripted brain — proves the pipeline, no tokens."""
    scripted = ScriptedBrain([
        {"say": "Of course! For what night, and how many in your party?", "done": False, "booking": None},
        {"say": "Friday at 7 for six — got it. And a name and number for the table?", "done": False, "booking": None},
        {"say": "Perfect, Dave — you're down for Friday 7pm, party of six. See you then!",
         "done": True, "booking": {"name": "Dave", "phone": "610-555-0148",
                                   "datetime": "Fri 7:00pm", "party_size": "6", "notes": "birthday"}},
    ])
    caller_lines = [
        "Hi, do you take reservations?",
        "Friday night, party of six.",
        "It's Dave, 610-555-0148, it's for a birthday.",
    ]
    call = VoiceCall(business, brain=scripted)
    print("AI :", await call.greeting())
    for line in caller_lines:
        print("YOU:", line)
        say = await call.hear(line)
        print("AI :", say)
        if call.done:
            break

    state = RuntimeState.load_or_new(business.business_id)
    await hand_to_owner(business, state, call.summary())
    print("\n✓ call complete · booking captured:", call.booking)
    print("✓ owner approval queued · pending now:", state.pending_count)


async def run_repl(business) -> None:
    """Talk to the receptionist in the terminal using the real claude -p brain."""
    call = VoiceCall(business, brain=ClaudeCLIBrain())
    print("AI :", await call.greeting())
    print("(type a caller line, or 'quit')")
    loop = asyncio.get_event_loop()
    while not call.done:
        line = await loop.run_in_executor(None, input, "YOU: ")
        if line.strip().lower() in {"quit", "exit"}:
            break
        print("AI :", await call.hear(line))
    if call.booking:
        state = RuntimeState.load_or_new(business.business_id)
        await hand_to_owner(business, state, call.summary())
        print("✓ booking handed to owner:", call.booking)


def build_fastapi_app(business):
    """Production server (optional dep). GET /voice/twiml + WS /voice/ws."""
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import PlainTextResponse
    import os, json as _json

    app = FastAPI()
    wss_url = os.environ.get("VOICE_WSS_URL", "wss://your-runtime.example/voice/ws")

    @app.get("/voice/twiml")
    def _twiml():
        return PlainTextResponse(twiml(business, wss_url), media_type="text/xml")

    @app.websocket("/voice/ws")
    async def _ws(ws: WebSocket):
        await ws.accept()
        call = VoiceCall(business, brain=ClaudeCLIBrain())
        async for raw in ws.iter_text():
            msg = _json.loads(raw)
            # ConversationRelay sends {"type":"prompt","voicePrompt":"..."} per caller turn.
            if msg.get("type") == "prompt":
                say = await call.hear(msg.get("voicePrompt", ""))
                await ws.send_text(_json.dumps({"type": "text", "token": say, "last": True}))
                if call.done:
                    state = RuntimeState.load_or_new(business.business_id)
                    await hand_to_owner(business, state, call.summary())
        return
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PAIS voice receptionist")
    ap.add_argument("--business", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true", help="scripted call, no tokens")
    g.add_argument("--repl", action="store_true", help="talk in terminal (claude -p brain)")
    g.add_argument("--serve", action="store_true", help="run FastAPI phone server (needs fastapi+uvicorn)")
    args = ap.parse_args()

    business = biz.load(args.business)
    if args.serve:
        import uvicorn
        uvicorn.run(build_fastapi_app(business), host="0.0.0.0", port=8766)
    else:
        asyncio.run(run_selftest(business) if args.selftest else run_repl(business))


if __name__ == "__main__":
    main()
