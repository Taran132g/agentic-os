"""
SMS tool — two delivery modes, chosen per business in config.

  • "owner-send"  (default): the approved draft is routed to the OWNER's phone
    (via Telegram) and they send it from their own number. No A2P 10DLC brand
    registration, no TCPA consent ledger on our side — the owner already has
    the customer relationship. This is what makes SMS viable for a solo builder.

  • "provider"    : real automated sending via Twilio/etc. Requires A2P 10DLC
    registration + documented opt-in. Only flip this on once a client is
    committed and registered.
"""

from __future__ import annotations


def send(business, to: str, body: str, *, send_telegram=None) -> dict:
    mode = business.channels.get("sms")
    if mode == "owner-send":
        return _route_to_owner(business, to, body, send_telegram)
    if mode == "provider":
        raise NotImplementedError("Twilio path — needs A2P 10DLC registration first")
    return {"ok": False, "reason": "sms disabled for this business"}


def _route_to_owner(business, to: str, body: str, send_telegram) -> dict:
    """Hand the ready-to-send text to the owner to fire from their own phone."""
    chat_id = business.channels.get("telegram_chat_id")
    msg = f"📲 Send to {to}:\n\n{body}"
    if send_telegram and chat_id:
        send_telegram(chat_id, msg)
        return {"ok": True, "mode": "owner-send", "delivered_to_owner": True}
    return {"ok": False, "reason": "no telegram_chat_id configured"}
