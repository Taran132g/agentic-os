"""
ElevenLabs voice receptionist — the restaurant-host agent.

This REPLACES the per-turn `claude -p` brain in voice_workflow.py for the live
phone/voice experience. With ElevenLabs Agents, ElevenLabs owns the whole call
(voice + speech-to-text + the conversational LLM + low-latency turn-taking).
Our code stops being the "brain" and becomes the *booking backend*:

  • build_agent_config(business)  → the host-agent definition (persona, first
    message, the booking workflow, and the two tools the agent calls). Source of
    truth for both the API-provision path and the dashboard-setup path.
  • provision_agent(business)     → create/update the agent via the ElevenLabs
    API and remember its agent_id (one-time per restaurant).
  • signed_url(business)          → short-lived wss:// URL the browser/phone uses
    to connect to that restaurant's agent without exposing the API key.
  • handle_post_call(business, payload) → ElevenLabs post-call webhook → pull the
    captured reservation out of the transcript and drop it into the SAME owner
    approval queue the old voice flow used (hand_to_owner / state.queue_approval).

The two tools the agent calls during a conversation:
  • check_availability(day, time, party_size) -> "open" | "limited" | "full"
  • book_table(name, phone, day, time, party_size, notes) -> confirmation text

For the in-browser demo these are CLIENT tools (the browser answers them and
updates the booking card). For a real phone line they become SERVER tools that
hit this module's webhook endpoints. Same names, same params — only the
transport differs, so a demo agent promotes to a phone agent unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)
KEY = "voice"

API_BASE = "https://api.elevenlabs.io/v1"
# A safe, stable default. Swap for a warmer host voice in the dashboard and pin
# it per-business via businesses/<id>.json -> voice.elevenlabs_voice_id.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"
DEFAULT_LLM = "gemini-2.0-flash"           # fast + cheap; latency matters on a call

AGENT_MAP_PATH = Path(__file__).parent / ".state" / "elevenlabs_agents.json"
ENV_FILE = Path(__file__).parent.parent / ".env"   # ~/agentic_os/.env (gitignored)
VAULT_KEYS = (Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian"
              / "Documents" / "Digital Brain" / "Projects & Building" / "API Keys.md")
# Same chain the content pipeline rotates (content_pipeline.py).
ENV_KEY_VARS = ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY_FALLBACK",
                "ELEVENLABS_API_KEY_FALLBACK_2", "XI_API_KEY")


# ── credentials ──────────────────────────────────────────────────────────────
def _keys_from_env_file() -> list[str]:
    if not ENV_FILE.exists():
        return []
    found: list[str] = []
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(ELEVENLABS_API_KEY[A-Z0-9_]*)\s*=\s*[\"']?([^\"'\s]+)", line)
        if m and m.group(2):
            found.append(m.group(2))
    return found


def _key_from_vault() -> str | None:
    if not VAULT_KEYS.exists():
        return None
    for line in VAULT_KEYS.read_text(encoding="utf-8").splitlines():
        if "eleven" in line.lower():
            m = re.search(r"(sk_[A-Za-z0-9]+|[A-Fa-f0-9]{32,})", line)
            if m:
                return m.group(1)
    return None


def load_api_keys() -> list[str]:
    """All ElevenLabs keys, in rotation order: process env → .env file → vault.
    Free accounts get ~15 Conversational-AI min/month each, so the chain ≈ that
    many times the minutes. Never logged."""
    candidates: list[str] = [os.environ[v].strip() for v in ENV_KEY_VARS if os.environ.get(v)]
    candidates += _keys_from_env_file()
    vault = _key_from_vault()
    if vault:
        candidates.append(vault)
    seen: set[str] = set()
    keys = [k for k in candidates if k and not (k in seen or seen.add(k))]
    if not keys:
        raise RuntimeError(
            "No ElevenLabs API key. Set ELEVENLABS_API_KEY, add it to .env, or to "
            f"the vault API Keys note ({VAULT_KEYS.name})."
        )
    return keys


def load_api_key() -> str:
    """First key in the chain (back-compat)."""
    return load_api_keys()[0]


def _fp(key: str) -> str:
    """Stable non-secret fingerprint for a key (maps keys → agent_ids safely)."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ── prompt + config (the host persona) ───────────────────────────────────────
def _voice_block(business) -> dict:
    """The optional `voice` config block on the business JSON (hours, etc.)."""
    return business.extra.get("voice", {}) if hasattr(business, "extra") else {}


def system_prompt(business) -> str:
    v = _voice_block(business)
    hours = v.get("hours", "")
    address = v.get("address", "")
    highlights = v.get("highlights", "")
    reservations = v.get("reservations", "We take reservations.")
    rules = v.get("rules", "Never take payment or card info over the phone.")
    return f"""You are the host answering the phone for {business.name}, a \
{business.vertical} business. You sound like a warm, real person on the phone — \
short spoken sentences, one question at a time, never robotic.

Voice / personality: {business.brand_voice}

What you know:
- {hours}
- {highlights}
- {reservations}
- Address: {address}

Your job is to BOOK A TABLE. Collect, one natural question at a time:
party size → day → time → name. Understand vague or messy input (a past date, \
"sometime Friday-ish") and handle it gracefully — never slot nonsense.

ALWAYS check the table before you promise it:
1. Once you have party size, day, and the time they want, call the \
`check_availability` tool with that day, time, and party size.
2. If it returns "full", say so warmly and offer the 1–2 nearest open times that \
day. If "limited", book it but mention it's filling up. If "open", proceed.
3. After you have an AVAILABLE slot plus their name, read the whole reservation \
back and ask them to confirm.
4. When they confirm, call the `book_table` tool with name, phone (if given), \
day, time, party size, and any notes. Then warmly confirm what happens next.

Rules: {rules} Keep every reply to one or two short spoken sentences. If you \
don't know something, offer to have a manager call back — never make it up."""


def first_message(business) -> str:
    return f"Thanks for calling {business.name}! How can I help you tonight?"


def _tool_defs() -> list[dict]:
    """Client/server tool schemas. Names + params must match the browser handlers
    (voice-demo.js) and the server webhooks below."""
    return [
        {
            "type": "client",
            "name": "check_availability",
            "description": "Check whether a table is open for a given day, time, "
                           "and party size. Returns 'open', 'limited', or 'full'.",
            "expects_response": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {"type": "string", "description": "e.g. Friday"},
                    "time": {"type": "string", "description": "e.g. 7:00 PM"},
                    "party_size": {"type": "string", "description": "e.g. 6"},
                },
                "required": ["day", "time", "party_size"],
            },
        },
        {
            "type": "client",
            "name": "book_table",
            "description": "Commit a confirmed reservation. Call only after the "
                           "caller confirms an available slot.",
            "expects_response": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name on the reservation"},
                    "phone": {"type": "string", "description": "Caller's phone number, if given"},
                    "day": {"type": "string", "description": "Day of the reservation, e.g. Friday"},
                    "time": {"type": "string", "description": "Time of the reservation, e.g. 7:00 PM"},
                    "party_size": {"type": "string", "description": "Number of guests, e.g. 6"},
                    "notes": {"type": "string", "description": "Occasion or special requests"},
                },
                "required": ["name", "day", "time", "party_size"],
            },
        },
    ]


def build_agent_config(business) -> dict:
    """The full ElevenLabs agent definition for this restaurant."""
    v = _voice_block(business)
    voice_id = v.get("elevenlabs_voice_id", DEFAULT_VOICE_ID)
    return {
        "name": f"{business.name} — Host",
        "conversation_config": {
            "agent": {
                "first_message": first_message(business),
                "language": "en",
                "prompt": {
                    "prompt": system_prompt(business),
                    "llm": v.get("elevenlabs_llm", DEFAULT_LLM),
                    "tools": _tool_defs(),
                },
            },
            "tts": {"voice_id": voice_id},
            # Hard cap per call — the main abuse guardrail for a PUBLIC demo so a
            # single caller can't drain the free monthly minutes. Override per
            # business with voice.max_call_seconds.
            "conversation": {"max_duration_seconds": int(v.get("max_call_seconds", 300))},
        },
    }


# ── agent_id store ───────────────────────────────────────────────────────────
def _load_agent_map() -> dict:
    if AGENT_MAP_PATH.exists():
        try:
            return json.loads(AGENT_MAP_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def get_agent_id(business_id: str, key: str) -> str | None:
    """The agent_id provisioned for this restaurant ON this specific account."""
    return _load_agent_map().get(business_id, {}).get(_fp(key))


def _save_agent_id(business_id: str, key: str, agent_id: str) -> None:
    AGENT_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    full = _load_agent_map()
    per_biz = {**full.get(business_id, {}), _fp(key): agent_id}
    AGENT_MAP_PATH.write_text(json.dumps({**full, business_id: per_biz}, indent=2),
                              encoding="utf-8")


# ── API calls ────────────────────────────────────────────────────────────────
def _api(method: str, path: str, body: dict | None = None, *, key: str) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", key)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"ElevenLabs API {e.code} on {method} {path}: {detail}") from e


def _provision_one(business, key: str) -> str:
    existing = get_agent_id(business.business_id, key)
    if existing:
        return existing
    resp = _api("POST", "/convai/agents/create", build_agent_config(business), key=key)
    agent_id = resp.get("agent_id") or resp.get("id")
    if not agent_id:
        raise RuntimeError(f"No agent_id in create response: {resp}")
    _save_agent_id(business.business_id, key, agent_id)
    log.info("Provisioned agent %s for %s on key …%s", agent_id, business.business_id, _fp(key)[:4])
    return agent_id


def provision_agent(business, *, all_keys: bool = True) -> dict:
    """Create the host agent on every key in the chain (so we can rotate when one
    runs out of free minutes). Returns {key_fingerprint: agent_id}."""
    keys = load_api_keys() if all_keys else load_api_keys()[:1]
    return {_fp(k): _provision_one(business, k) for k in keys}


def update_agent(business, key: str) -> str:
    """Push the current config (prompt/voice/tools/guardrails) to an existing
    agent on this account, or create it if missing."""
    agent_id = get_agent_id(business.business_id, key)
    if not agent_id:
        return _provision_one(business, key)
    _api("PATCH", f"/convai/agents/{agent_id}", build_agent_config(business), key=key)
    log.info("Updated agent %s on key …%s", agent_id, _fp(key)[:4])
    return agent_id


def update_agents(business) -> dict:
    """Patch the agent on every key in the chain. Returns {fingerprint: agent_id}."""
    return {_fp(k): update_agent(business, k) for k in load_api_keys()}


def agent_pairs(business) -> list[dict]:
    """(key_fingerprint, agent_id) for each account — used to populate the public
    rotation function's env. Never includes the secret key itself."""
    full = _load_agent_map().get(business.business_id, {})
    return [{"fp": _fp(k), "agent_id": full.get(_fp(k))} for k in load_api_keys()]


def signed_url(business) -> str:
    """Short-lived wss:// URL to connect to this restaurant's agent.

    Rotates the key chain: if one account is out of free Conversational-AI minutes
    (or its agent is missing), fall through to the next. Auto-provisions a missing
    agent on a healthy key so the chain self-heals."""
    errors: list[str] = []
    for key in load_api_keys():
        try:
            agent_id = get_agent_id(business.business_id, key) or _provision_one(business, key)
            q = urllib.parse.urlencode({"agent_id": agent_id})
            resp = _api("GET", f"/convai/conversation/get-signed-url?{q}", key=key)
            url = resp.get("signed_url")
            if url:
                return url
            errors.append(f"…{_fp(key)[:4]}: no signed_url")
        except Exception as e:  # quota/401/etc → try the next key in the chain
            errors.append(f"…{_fp(key)[:4]}: {e}")
    raise RuntimeError("All ElevenLabs keys exhausted or failing: " + " | ".join(errors))


# ── post-call webhook → owner approval queue ─────────────────────────────────
def _booking_from_payload(payload: dict) -> dict | None:
    """Pull a reservation out of an ElevenLabs post-call webhook payload.

    Prefers structured `data_collection_results`; falls back to None. The shape
    has varied across ElevenLabs versions, so we read defensively.
    """
    data = payload.get("data", payload)
    analysis = data.get("analysis", {}) or {}
    collected = analysis.get("data_collection_results", {}) or {}

    def pick(*keys: str) -> str:
        for k in keys:
            item = collected.get(k)
            if isinstance(item, dict) and item.get("value"):
                return str(item["value"])
            if isinstance(item, str) and item:
                return item
        return ""

    booking = {
        "name": pick("name", "guest_name"),
        "phone": pick("phone", "phone_number"),
        "datetime": " ".join(x for x in (pick("day", "date"), pick("time")) if x).strip(),
        "party_size": pick("party_size", "party"),
        "notes": pick("notes", "occasion"),
    }
    return booking if any(booking.values()) else None


def queue_owner_booking(business, state, booking: dict | None,
                        transcript: list | None = None) -> dict:
    """Drop a captured reservation into the existing owner-approval queue.

    Shared by the browser CLIENT-tool path (book_table) and the phone post-call
    webhook. `booking` uses hand_to_owner's shape:
    {name, phone, datetime, party_size, notes}.
    """
    import asyncio
    from .voice_workflow import hand_to_owner
    summary = {
        "business_id": business.business_id,
        "transcript": transcript or [],
        "booking": booking or None,
        "done": True,
    }
    asyncio.run(hand_to_owner(business, state, summary))
    return {"ok": True, "booking": booking}


def handle_post_call(business, state, payload: dict) -> dict:
    """Map a finished ElevenLabs phone call onto the owner-approval queue."""
    booking = _booking_from_payload(payload)
    transcript = (payload.get("data", payload) or {}).get("transcript", [])
    return queue_owner_booking(business, state, booking, transcript)


# ── CLI ──────────────────────────────────────────────────────────────────────
def _dashboard_summary(business) -> str:
    cfg = build_agent_config(business)
    agent = cfg["conversation_config"]["agent"]
    lines = [
        f"# Dashboard setup for: {cfg['name']}",
        "",
        "1. ElevenLabs → Agents → Create agent.",
        f"2. Voice: pick a warm host voice (config voice_id = "
        f"{cfg['conversation_config']['tts']['voice_id']}).",
        f"3. LLM: {agent['prompt']['llm']} (any fast model).",
        "4. First message:",
        f"   {agent['first_message']}",
        "5. System prompt: paste the block printed by `--prompt`.",
        "6. Add two CLIENT tools (exact names): check_availability, book_table",
        "   — params are printed by `--config`. The browser supplies the handlers.",
        "7. Copy the agent_id → set ELEVENLABS_AGENT_ID or run `--provision`.",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse
    from . import business as biz

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="ElevenLabs restaurant-host agent")
    ap.add_argument("--business", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt", action="store_true", help="print the system prompt")
    g.add_argument("--config", action="store_true", help="print the full agent config JSON")
    g.add_argument("--setup", action="store_true", help="print dashboard setup steps")
    g.add_argument("--provision", action="store_true", help="create the agent on EVERY key in the chain")
    g.add_argument("--update", action="store_true", help="patch the live agents with the current config")
    g.add_argument("--signed-url", action="store_true", help="print a connect URL (rotates the chain)")
    g.add_argument("--keys", action="store_true", help="show the key chain + each account's tier")
    g.add_argument("--selftest", action="store_true", help="offline: build config + simulate a post-call booking")
    args = ap.parse_args()

    business = biz.load(args.business)
    if args.prompt:
        print(system_prompt(business))
    elif args.config:
        print(json.dumps(build_agent_config(business), indent=2))
    elif args.setup:
        print(_dashboard_summary(business))
    elif args.provision:
        for fp, aid in provision_agent(business).items():
            print(f"key …{fp[:4]} → agent {aid}")
    elif args.update:
        for fp, aid in update_agents(business).items():
            print(f"updated key …{fp[:4]} → agent {aid}")
    elif args.signed_url:
        print(signed_url(business))
    elif args.keys:
        _show_keys()
    elif args.selftest:
        _run_selftest(business)


def _show_keys() -> None:
    """Read-only: list the rotation chain and each account's plan tier."""
    keys = load_api_keys()
    print(f"{len(keys)} ElevenLabs key(s) in rotation:")
    for i, k in enumerate(keys):
        try:
            sub = _api("GET", "/user/subscription", key=k)
            tier = sub.get("tier", "?")
            note = "~15 convai min/mo" if tier == "free" else "paid plan"
        except Exception as e:
            tier, note = "ERR", str(e)[:60]
        print(f"  {i+1}. …{_fp(k)[:4]}  tier={tier}  ({note})")


def _run_selftest(business) -> None:
    """No network, no key, no tokens — proves config build + booking handoff."""
    from .state import RuntimeState
    cfg = build_agent_config(business)
    tools = [t["name"] for t in cfg["conversation_config"]["agent"]["prompt"]["tools"]]
    assert tools == ["check_availability", "book_table"], tools
    assert business.name in cfg["conversation_config"]["agent"]["first_message"]
    print(f"✓ agent config built for {business.name} · tools={tools}")

    fake_payload = {"data": {
        "transcript": [{"role": "agent", "message": "Thanks for calling!"}],
        "analysis": {"data_collection_results": {
            "name": {"value": "Dave"},
            "phone": {"value": "610-555-0148"},
            "day": {"value": "Friday"}, "time": {"value": "7:00 PM"},
            "party_size": {"value": "6"}, "notes": {"value": "birthday"},
        }},
    }}
    booking = _booking_from_payload(fake_payload)
    print("✓ parsed booking from post-call payload:", booking)

    state = RuntimeState.load_or_new(business.business_id)
    before = state.pending_count
    result = handle_post_call(business, state, fake_payload)
    state = RuntimeState.load_or_new(business.business_id)
    print("✓ owner approval queued ·", result["ok"],
          "· pending", before, "→", state.pending_count)


if __name__ == "__main__":
    main()
