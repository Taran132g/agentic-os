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
  NOTE: still fill this in even for a "market" entry_type if he names the price he
  got in at — it's a useful reference — but the order will still be placed at market.
- "entry_type": "limit" or "market", decided from INTENT, not just whether a price exists:
    * "limit"  — he is telling you to PLACE AN ORDER AT a price / wait for an entry /
                 gives a buy-limit or an entry zone to get filled into
                 ("buy limit 64k", "set a limit at...", "enter on retest of 3.20", "bid 0.95-0.98").
    * "market" — enter NOW at the current price. Use this when he says "buy now",
                 "market", "entering now", OR when he REPORTS AN ALREADY-TAKEN position
                 ("I bought X at $Y", "I'm in at $Y", "just longed X", "grabbed some here").
                 You are following him in, so you take the CURRENT price, not a resting
                 limit at his fill (which may never fill if it has already moved).
  If unclear: a bare price framed as an instruction => "limit"; a live call or a report
  of his own buy => "market".
- "entry_low"/"entry_high": the range bounds if a zone was given (e.g. 68k-69k),
  else null. Keep k-suffixes expanded.
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


async def _claude_parse(text: str, risk_usd: float, broadcast=None) -> dict | None:
    """Parse the signal with a Claude subprocess. Returns None on any failure."""
    from tools.llm import run_llm_command

    try:
        res = await run_llm_command(
            prompt=_PARSE_PROMPT.format(signal_text=text[:2000], risk_usd=risk_usd),
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


_CLASSIFY_PROMPT = """Classify this message from a trading signal channel.

Is it an ACTIONABLE trade signal — a buy/sell/entry/exit call the reader could act on
right now (a new position, an add, or a close) — as opposed to market commentary,
analysis, a recap of an old trade, a question, or chatter?

Message:
<<<
{text}
>>>

Reply with ONLY a JSON object on one line, no prose:
{{"is_signal": true|false, "confidence": 0-100, "direction": "LONG"|"SHORT"|"none"}}
- confidence = how sure you are it's an actionable signal. Commentary / analysis /
  recaps / questions score below 40.
- direction = the trade side if it is a signal, else "none"."""


async def _classify_signal(text: str, broadcast=None) -> dict | None:
    """
    Cheap first-pass gate (Haiku): is this an actionable signal + how confident?
    Runs on every routed post so the pricier Sonnet extraction only fires on real
    signals. Returns {"is_signal","confidence","direction"} or None on failure.
    """
    from tools.llm import run_llm_command
    try:
        res = await run_llm_command(
            prompt=_CLASSIFY_PROMPT.format(text=text[:1200]),
            broadcast=broadcast, allowed_tools="", agent_name="classify")
    except Exception as e:
        log.warning("[live_signal] classify raised: %s", e)
        return None
    raw = res.get("result", "") if isinstance(res, dict) else str(res)
    return _extract_json(raw)


# Phrases that mean "he's already in / enter now" => market in, don't rest a limit.
_MARKET_INTENT = re.compile(
    r"\b(bought|buying|longed|shorted|grabbed|got in|i'?m in|entered|market|now|aped)\b",
    re.I,
)
# Phrases that mean "place a resting order at a level" => limit.
_LIMIT_INTENT = re.compile(r"\b(limit|bid|set (?:a )?(?:buy|order)|retest|wait for)\b", re.I)


def _infer_entry_type(text: str, entry) -> str:
    """Heuristic order intent when the LLM didn't tag one (fallback path)."""
    if _MARKET_INTENT.search(text) and not _LIMIT_INTENT.search(text):
        return "market"
    if _LIMIT_INTENT.search(text):
        return "limit"
    return "limit" if entry is not None else "market"


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
        "entry_type":   _infer_entry_type(text, sig["entry"]),
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
    def _f(key):
        v = parsed.get(key)
        return float(v) if v is not None else None
    try:
        entry = _f("entry")
        elo   = _f("entry_low")
        ehi   = _f("entry_high")
        sl    = _f("stop_loss")
        tps   = [float(t) for t in (parsed.get("take_profits") or []) if t]
        lev   = min(max(int(parsed.get("leverage") or 1), 1), 50)
        conf  = max(0, min(100, int(parsed.get("confidence") or 0)))
    except (TypeError, ValueError):
        return None
    ac = parsed.get("asset_class")
    et = str(parsed.get("entry_type", "")).lower().strip()
    entry_type = et if et in ("limit", "market") else ("limit" if entry is not None else "market")
    return {
        "asset":        asset,
        "asset_class":  ac if ac in ("crypto", "stock") else "crypto",
        "direction":    "SHORT" if str(parsed.get("direction", "")).upper() == "SHORT" else "LONG",
        "entry":        entry,
        "entry_type":   entry_type,
        "entry_low":    elo,
        "entry_high":   ehi,
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
    from tools.trade_tracker import add_trade, resolve_risk_usd

    risk = resolve_risk_usd()
    parsed = await _claude_parse(text, risk_usd=risk, broadcast=broadcast)
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
        risk_usd     = risk,
    )
    if not sizing.get("ok"):
        return {"ok": False, "error": sizing.get("error", "Sizing failed."), "parsed": sig}

    from tools.position_sizer import best_entry_in_zone
    best_entry = best_entry_in_zone(
        sig.get("entry_low"), sig.get("entry_high"), sig["direction"],
        fallback=sizing["entry"])

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
        exit_plan    = sizing.get("exit_plan"),
        extra = {
            "best_entry":        best_entry,
            "entry_low":         sig.get("entry_low"),
            "entry_high":        sig.get("entry_high"),
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
    return {"ok": True, "trade": trade, "sizing": sizing, "parsed": sig,
            "best_entry": best_entry}
