"""Persona/fields injection for PAIS script agents (2026-06-11).

When an agent is run from the PAIS web Control Room, the bridge exports the
user's configured persona + fields as env vars (PAIS_PERSONA, PAIS_FIELDS).
Scripts append `persona_block()` to their claude prompts so the settings
actually steer the real run. Scheduled n8n runs have no env set → empty block,
behavior unchanged.
"""

import json
import os


def persona_block() -> str:
    """The user's agent settings as a prompt block, or "" when not configured."""
    persona = os.environ.get("PAIS_PERSONA", "").strip()
    try:
        fields = json.loads(os.environ.get("PAIS_FIELDS", "") or "{}")
    except Exception:
        fields = {}
    fields = {k: str(v).strip() for k, v in (fields or {}).items()
              if str(v).strip() and k != "ROUTINE"}
    if not persona and not fields:
        return ""
    lines = ["", "USER'S AGENT SETTINGS (configured in their PAIS Control Room — honor these):"]
    if persona:
        lines.append(f"- persona / how to work: {persona}")
    for k, v in fields.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"
