"""Shared local-business outreach pitch — the SINGLE SOURCE OF TRUTH.

Imported by both outreach agents so the template + signature can never drift:
  - ~/agentic_os/piontrix_outreach.py   (n8n batch outreach)
  - ~/pais-runtime/agents.py            (morning-routine outreach)

Edit the pitch HERE and both agents pick it up. We do NOT name the business as
someone Taran already helps (we can't claim that); the pitch is that PAIS helps
local businesses compete with the big chains cheaply.
"""

# Reproduced near-verbatim per business — only the "money leaks like …" examples
# clause is tailored to the business type (keep two short examples, same shape).
PITCH_TEMPLATE = (
    "Hi, is this the owner? I'll be quick — my name's Taran, I'm local here in "
    "Collegeville. I help local businesses compete on the same level as the big "
    "chains, for a fraction of the price — plugging the money leaks like the calls "
    "you miss when it's slammed, and the regulars who quietly stop coming in. I'm "
    "setting the first few places up free for 30 days. Can I swing by and show you "
    "what it'd look like — ten minutes?"
)

# Full https URL so it stays a clickable link in plaintext email clients.
SIGNATURE = "Taran\nPAIS · https://getpais.company"

# Marker used to avoid appending the signature twice.
SIGNATURE_MARKER = "getpais.company"


def with_signature(body: str) -> str:
    """Return body with SIGNATURE appended once (no-op if already present)."""
    if SIGNATURE_MARKER in body:
        return body
    return body.rstrip() + "\n\n" + SIGNATURE
