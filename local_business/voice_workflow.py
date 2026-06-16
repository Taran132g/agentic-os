"""
Voice Receptionist agent.

The alternative to Missed-Call Text-Back: instead of texting people back AFTER a
call rings out, the AI picks up the phone so the call is never missed in the
first place. It greets the caller, answers hours / location / basic menu
questions, takes a reservation or party request, and hands a clean summary to
the owner.

Design (from the 2026 voice-AI research):
  • Telephony + STT + TTS are owned by the platform (Twilio ConversationRelay
    by default) so we don't touch audio. We only supply the *words*.
  • The "brain" is pluggable. For dev/demo it runs on `claude -p` (the
    customer's own Claude subscription, no API key) via tools.llm. For a
    production low-latency phone line you swap in a realtime/streaming model
    (Twilio ConversationRelay, OpenAI Realtime, or a Pipecat/LiveKit pipeline)
    — `claude -p` spawns a CLI per turn and is too slow for live audio, so it's
    dev-only. The brain interface is one async call, so swapping is trivial.

This module is transport-agnostic: it turns a running transcript into the next
thing to say + any structured booking captured. voice_server.py wires it to a
phone line (or a terminal REPL for demos).
"""

from __future__ import annotations

import json
import logging

from .base import parse_json

log = logging.getLogger(__name__)
KEY = "voice"


def system_prompt(business) -> str:
    booking = business.integrations.get("booking") or "no booking system — capture name + number + party details for the owner to confirm"
    return f"""You are the voice receptionist answering the phone for {business.name},
a {business.vertical} business. You sound like a warm, real person on the phone —
short spoken sentences, never a wall of text, never robotic.

Voice/persona: {business.brand_voice}

Booking: {booking}.

Your job on a call:
  1. Greet, find out what they need (a table/appointment, a party/event, hours,
     directions, or a simple question).
  2. For a booking/party: collect name, phone number, date/time, and party size —
     one question at a time, conversationally.
  3. Answer simple questions from what you know; if unsure, say you'll have
     someone call back rather than make something up.
  4. Close warmly and confirm what happens next.

After EACH caller turn, respond with ONLY this JSON (no prose around it):
{{
  "say": "<the next thing to say out loud — one or two short sentences>",
  "done": false,
  "booking": null
}}
When the call is complete, set "done": true and, if you captured one, fill
"booking" with {{"name","phone","datetime","party_size","notes"}}.
"""


class Brain:
    """Pluggable LLM backend. Default uses claude -p; tests/prod inject another."""

    async def reply(self, business, transcript: list[dict]) -> dict:
        raise NotImplementedError


class ClaudeCLIBrain(Brain):
    """Dev/demo brain — runs each turn through `claude -p` (no API key)."""

    async def reply(self, business, transcript: list[dict]) -> dict:
        from tools.llm import run_llm_command
        convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)
        prompt = (system_prompt(business)
                  + "\n\nConversation so far:\n" + convo
                  + "\n\nReturn ONLY the JSON for your next turn.")
        result = await run_llm_command(prompt, allowed_tools="",
                                       agent_name=f"{business.business_id}:{KEY}")
        out = parse_json(result.get("result", ""))
        if isinstance(out, list) and out:
            out = out[0]
        return out if isinstance(out, dict) else {"say": "Sorry, could you repeat that?", "done": False, "booking": None}


class ScriptedBrain(Brain):
    """Deterministic brain for offline tests/demos — no LLM, no tokens."""

    def __init__(self, turns: list[dict]):
        self._turns = list(turns)

    async def reply(self, business, transcript: list[dict]) -> dict:
        return self._turns.pop(0) if self._turns else {"say": "Thanks for calling — bye!", "done": True, "booking": None}


class VoiceCall:
    """One live call. Feed it caller utterances; it yields what to say back."""

    def __init__(self, business, brain: Brain | None = None):
        self.business = business
        self.brain = brain or ClaudeCLIBrain()
        self.transcript: list[dict] = []
        self.booking: dict | None = None
        self.done = False

    async def greeting(self) -> str:
        line = f"Thanks for calling {self.business.name}! How can I help you?"
        self.transcript.append({"role": "assistant", "text": line})
        return line

    async def hear(self, caller_text: str) -> str:
        """Caller said something → return what the receptionist says next."""
        self.transcript.append({"role": "caller", "text": caller_text})
        turn = await self.brain.reply(self.business, self.transcript)
        say = turn.get("say", "Sorry, could you say that again?")
        self.transcript.append({"role": "assistant", "text": say})
        if turn.get("booking"):
            self.booking = turn["booking"]
        if turn.get("done"):
            self.done = True
        return say

    def summary(self) -> dict:
        return {"business_id": self.business.business_id,
                "transcript": self.transcript, "booking": self.booking, "done": self.done}


async def hand_to_owner(business, state, call_summary: dict) -> None:
    """A finished call → queue the captured booking for the owner to confirm."""
    from .base import AGENT_META
    b = call_summary.get("booking")
    meta = AGENT_META.get("missedcall", {"ic": "☎", "ac": "var(--green)"})
    if b:
        who = f"Confirm {b.get('name','a caller')}'s booking"
        ctx = (f"<b>AI answered live</b> and booked: {b.get('party_size','?')} "
               f"on {b.get('datetime','?')}. {b.get('notes','')}".strip())
        state.queue_approval(agent="missedcall", who=who, meta="AI answered the call",
                             context=ctx, channel="Auto-added to your book — just confirm",
                             draft=f"{b.get('name','')} · {b.get('phone','')} · "
                                   f"{b.get('datetime','')} · party {b.get('party_size','')}",
                             value=180)
        state.bump_metric("calls")
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body=f"<b>Voice</b> answered a call and booked {b.get('name','a guest')}")
    else:
        state.add_activity(ic=meta["ic"], ac=meta["ac"],
                           body="<b>Voice</b> answered a call (question only — no booking)")
    state.save()
