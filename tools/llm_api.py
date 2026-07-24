"""
Direct Anthropic Messages API backend for the executor's LLM calls.

Used on a machine with no `claude` CLI (e.g. Oracle) — set `PAIS_LLM_BACKEND=api`.
The executor's parse + risk-gate both call `run_llm_command(..., allowed_tools="")`
(pure text, no tools), so a plain Messages API completion is a drop-in replacement.
Defaults to a cheap model (Haiku) since these are structured extraction + advisory
reasoning. Cost is bounded: only trade-intent posts trigger a call.

Never raises — returns {"result": text, "success": bool[, "error"]}.
"""

import logging
import os

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = 60.0

HAIKU  = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
OPUS   = "claude-opus-4-8"

# Per-stage model tiering to spend the fewest credits: a cheap model does the
# high-volume "is this even an actionable signal?" gate; a capable model only
# runs on posts that pass, for the money-relevant extraction + risk reasoning.
# Each is overridable by env so the tiers can be tuned without a code change.
DEFAULT_MODEL = os.environ.get("PAIS_LLM_API_MODEL", HAIKU)
_MODEL_BY_AGENT = {
    "classify":    os.environ.get("PAIS_LLM_MODEL_CLASSIFY", HAIKU),   # cheap filter gate
    "live_trader": os.environ.get("PAIS_LLM_MODEL_EXTRACT",  SONNET),  # signal extraction
    "risk_gate":   os.environ.get("PAIS_LLM_MODEL_RISK",     SONNET),  # risk reasoning
}


def _model_for(agent_name: str) -> str:
    return _MODEL_BY_AGENT.get(agent_name, DEFAULT_MODEL)


async def run_llm_api(prompt: str, agent_name: str = "", model: str | None = None,
                      max_tokens: int = 1500, **_) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"result": "", "success": False, "error": "ANTHROPIC_API_KEY not set"}
    model = model or _model_for(agent_name)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                API_URL,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        if r.status_code != 200:
            log.warning("[llm_api] HTTP %s for %s: %s", r.status_code, agent_name, r.text[:200])
            return {"result": "", "success": False, "error": f"HTTP {r.status_code}: {r.text[:150]}"}
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        return {"result": text, "success": True}
    except Exception as e:
        log.warning("[llm_api] call failed for %s: %s", agent_name, e)
        return {"result": "", "success": False, "error": str(e)}
