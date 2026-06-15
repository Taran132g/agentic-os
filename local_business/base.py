"""
Shared agent plumbing.

Every local-business agent follows the same arc:
    gather inputs → ask Claude to draft → queue for owner approval.

This module holds the two pieces they all share — a thin `draft()` wrapper over
the existing PAIS LLM runner (`tools.llm.run_llm_command`, i.e. `claude -p` on
the customer's own subscription) and `request_send()` for human-in-the-loop —
so each workflow file stays small and only carries its own prompt + logic.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Reuse the PAIS root tools (llm, approval, utils) without copying them.
PAIS_ROOT = Path(__file__).resolve().parent.parent
if str(PAIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PAIS_ROOT))

log = logging.getLogger(__name__)


async def draft(prompt: str, *, agent_name: str, broadcast=None) -> str:
    """Run a drafting prompt through Claude (customer's sub) and return raw text."""
    from tools.llm import run_llm_command
    result = await run_llm_command(
        prompt,
        broadcast=broadcast,
        allowed_tools="Bash,Read,WebSearch,WebFetch",  # read-only: agents draft, they don't send
        agent_name=agent_name,
    )
    return result.get("result", "").strip()


def parse_json(text: str):
    """Tolerant JSON extraction — reuses PAIS's extractor, falls back to []."""
    try:
        from tools.utils import extract_json
        return extract_json(text)
    except Exception:
        return []


async def request_send(action: str, details: str, *, owner_send: bool) -> bool:
    """
    Human-in-the-loop gate. Returns True only when the owner approves.

    For SMS where the owner is the sender, this also routes the approved draft
    to the owner's phone for them to send from their own number — which is why
    we never need A2P 10DLC / TCPA brand registration.
    """
    from tools.approval import ask
    note = " (you send from your own number)" if owner_send else ""
    return await ask(action + note, details)


# Per-agent display identity used on dashboard cards + activity feed.
AGENT_META = {
    "reputation":   {"ic": "★", "ac": "var(--blue)"},
    "reactivation": {"ic": "↩", "ac": "var(--brass)"},
    "missedcall":   {"ic": "☎", "ac": "var(--green)"},
    "reminders":    {"ic": "⏰", "ac": "var(--brass)"},
    "digest":       {"ic": "▤", "ac": "var(--brass)"},
}
