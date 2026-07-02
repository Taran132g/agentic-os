"""
Live Signal workflow — paste a Dr. Profit signal (crypto OR stock), get a
sized trade entry at fixed $60 risk.

Flow (triggered from the Live Trading tab):
  1. A Claude subprocess (via tools.llm, Taran's subscription) parses the
     raw signal text into structured fields — it handles free-text formats,
     stock tickers, "buy now" market entries, and multi-target signals that
     the regex parser in dr_profit_monitor can't.
  2. Python fetches the live price + volatility and sizes the position
     deterministically at $RISK_USD (tools.position_sizer) — the LLM never
     does the money math.
  3. The trade is written to the tracker with full sizing context.

Fail-safe: if the Claude parse fails, falls back to the regex parser
(crypto only) so a signal is never lost.
"""

import json
import logging
import re

log = logging.getLogger(__name__)

_PARSE_PROMPT = """You are PAIS's Live Trader agent. Taran pasted a raw trade signal from Dr. Profit
(a Telegram signal provider). It may be a CRYPTO trade or a STOCK pick, in any free-text format.

## Signal text
<<<
{signal_text}
>>>

## Sizing framework (context — do NOT do the math yourself)
- Taran risks a FIXED ${risk_usd:.0f} on every trade regardless of asset.
- Position size is computed in code as risk / |entry - stop|.
- If the signal gives no stop loss, code derives one from volatility (~1.5 sigma of the expected move).

## Your job
Extract the trade into JSON. Rules:
- "asset": the ticker symbol only, uppercase (BTC, ETH, NVDA, TSLA...). Strip $ prefixes.
- "asset_class": "crypto" or "stock". Decide from the ticker/context.
- "direction": "LONG" or "SHORT". Buy/accumulate/calls => LONG. Sell/short/puts => SHORT.
- "entry": number, or null if the signal says enter now / at market / gives no price.
  If an entry RANGE is given, use the midpoint. Interpret k-suffixes (65k = 65000).
- "stop_loss": number or null.
- "take_profits": array of numbers (may be empty). Order nearest-first.
- "leverage": integer, 1 if not mentioned. Cap at 50.
- "confidence": 0-100, how confident you are this is a real actionable trade signal
  (a price-chat or recap message is NOT a signal — score it below 40).
- "note": one sentence — your read of the setup (quality of levels, anything odd or missing).

If the text is NOT a trade signal at all, return {{"confidence": 0, "note": "why"}}.

Output ONLY the JSON object on a single line. No markdown fences, no prose."""


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def _claude_parse(text: str, broadcast=None) -> dict | None:
    """Parse the signal with a Claude subprocess. Returns None on any failure."""
    from tools.llm import run_llm_command
    from tools.position_sizer import RISK_USD

    try:
        res = await run_llm_command(
            prompt=_PARSE_PROMPT.format(signal_text=text[:2000], risk_usd=RISK_USD),
            broadcast=broadcast,
            allowed_tools="",   # pure extraction — no tools
            agent_name="live_trader",
        )
    except Exception as e:
        log.warning("[live_signal] Claude parse raised: %s", e)
        return None

    raw = res.get("result", "") if isinstance(res, dict) else str(res)
    parsed = _extract_json(raw)
    if not parsed:
        log.warning("[live_signal] No JSON in Claude output: %s", raw[:200])
        return None
    return parsed


def _regex_fallback(text: str) -> dict | None:
    """Crypto-only fallback via the monitor's regex parser."""
    from dr_profit_monitor import parse_signal
    sig = parse_signal(text)
    if not sig:
        return None
    return {
        "asset":        sig["asset"],
        "asset_class":  "crypto",
        "direction":    sig["direction"],
        "entry":        sig["entry"],
        "stop_loss":    sig.get("stop_loss"),
        "take_profits": sig.get("take_profit") or [],
        "leverage":     sig.get("leverage", 1),
        "confidence":   50,
        "note":         "Parsed by regex fallback (Claude parse unavailable).",
    }


def _normalize(parsed: dict) -> dict | None:
    """Validate + coerce the LLM output. Returns None if unusable."""
    asset = str(parsed.get("asset") or "").upper().strip().lstrip("$")
    if not asset or not re.fullmatch(r"[A-Z0-9.]{1,10}", asset):
        return None
    try:
        entry = float(parsed["entry"]) if parsed.get("entry") is not None else None
        sl    = float(parsed["stop_loss"]) if parsed.get("stop_loss") is not None else None
        tps   = [float(t) for t in (parsed.get("take_profits") or []) if t]
        lev   = min(max(int(parsed.get("leverage") or 1), 1), 50)
        conf  = max(0, min(100, int(parsed.get("confidence") or 0)))
    except (TypeError, ValueError):
        return None
    ac = parsed.get("asset_class")
    return {
        "asset":        asset,
        "asset_class":  ac if ac in ("crypto", "stock") else "crypto",
        "direction":    "SHORT" if str(parsed.get("direction", "")).upper() == "SHORT" else "LONG",
        "entry":        entry,
        "stop_loss":    sl,
        "take_profits": tps,
        "leverage":     lev,
        "confidence":   conf,
        "note":         str(parsed.get("note") or "")[:300],
    }


async def process_signal(text: str, broadcast=None) -> dict:
    """
    Full pipeline: parse → price/vol → size at $60 → create trade entry.
    Returns {"ok": True, "trade", "sizing", "parsed"} or {"ok": False, "error"}.
    """
    from tools.position_sizer import size_trade
    from tools.trade_tracker import add_trade

    parsed = await _claude_parse(text, broadcast=broadcast)
    if parsed is not None and int(parsed.get("confidence") or 0) < 40:
        return {"ok": False,
                "error": f"Doesn't look like an actionable signal: {parsed.get('note', 'low confidence')}"}
    sig = _normalize(parsed) if parsed else None
    if sig is None:
        sig = _regex_fallback(text)
    if sig is None:
        return {"ok": False,
                "error": "Could not parse a trade out of that text — check asset/direction/prices."}

    sizing = await size_trade(
        asset        = sig["asset"],
        asset_class  = sig["asset_class"],
        direction    = sig["direction"],
        entry        = sig["entry"],
        stop_loss    = sig["stop_loss"],
        take_profits = sig["take_profits"],
        leverage     = sig["leverage"],
    )
    if not sizing.get("ok"):
        return {"ok": False, "error": sizing.get("error", "Sizing failed."), "parsed": sig}

    trade = add_trade(
        asset        = sig["asset"],
        direction    = sig["direction"],
        entry_price  = sizing["entry"],
        stop_loss    = sizing["stop_loss"],
        take_profit  = sig["take_profits"],
        leverage     = sizing["leverage"],
        signal_text  = text,
        source       = "live_signal",
        risk_usd     = sizing["risk_usd"],
        asset_class  = sig["asset_class"],
        units        = sizing["units"],
        extra = {
            "stop_source":       sizing["stop_source"],
            "stop_pct":          sizing["stop_pct"],
            "annual_vol":        sizing["annual_vol"],
            "vol_source":        sizing["vol_source"],
            "expected_move_pct": sizing["expected_move_pct"],
            "rr_to_tp1":         sizing["rr_to_tp1"],
            "agent_note":        sig["note"],
            "confidence":        sig["confidence"],
        },
    )

    log.info("[live_signal] Entered %s %s %s @ %s — %s units, $%s risk",
             sig["asset_class"], sig["asset"], sig["direction"],
             sizing["entry"], sizing["units"], sizing["risk_usd"])
    return {"ok": True, "trade": trade, "sizing": sizing, "parsed": sig}
