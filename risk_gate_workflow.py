"""
Risk Gate — pre-trade evaluator for Dr. Profit signals.

Sits between `dr_profit_monitor.py` parsing a signal and the Telegram alert.
Runs a 3-debator + portfolio-manager pattern (modeled on
TauricResearch/TradingAgents risk_mgmt agents) to produce a structured verdict:

    {
        "verdict":            "APPROVE" | "RESIZE" | "REJECT",
        "confidence":         0..100,
        "suggested_risk_pct": float,   # % of bankroll
        "reasoning":          str,     # one-paragraph rationale
        "debate_summary":     str,     # aggressive vs conservative vs neutral
    }

The verdict is appended to the Telegram alert. Taran still decides whether to
act — the gate is advisory, not executable.

Fail-safe: any error returns a sentinel verdict so the monitor falls back to
the raw alert without blocking.
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
DR_PROFIT_PERF = VAULT / "Money & Markets" / "Dr-Profit" / "Dr-Profit-Performance-Analysis.md"


_RISK_GATE_PROMPT = """You are PAIS's Risk Gate, evaluating a Dr. Profit crypto signal BEFORE Taran acts on it.

You must role-play four distinct voices in sequence, then output a single JSON verdict.

## Context

### Signal received
- Asset:      {asset}
- Direction:  {direction}
- Entry:      ${entry:,.2f}
- Stop loss:  {stop_loss}
- Take profits: {take_profits}
- Leverage:   {leverage}x
- Raw text:   {raw}

### Position sizing (fixed-risk — already computed in code)
- The trade is sized so a stop-out loses a FIXED **$60**, whatever the asset: units = $60 / |entry - stop|.
- You are NOT allocating a percent of bankroll. Judge signal QUALITY and whether to take the full $60 or trim it.

### Bankroll snapshot
- Current bankroll:    ${bankroll:,.2f}
- Starting bankroll:   ${starting:,.2f}
- Realized PnL:        ${realized_pnl:+,.2f}
- Open PnL:            ${open_pnl:+,.2f}
- Win rate so far:     {win_rate}% ({wins}W / {losses}L)
- Open trades:         {open_trades}

### Existing exposure
{active_trades_block}

### Dr. Profit historical context
{dr_profit_context}

## Your job

Run a structured debate in your head, then output ONLY a JSON object.

1. AGGRESSIVE debator — argues FOR the trade. Cites momentum, conviction, Dr. Profit's track record, asymmetric upside. Bullish on size.

2. CONSERVATIVE debator — argues AGAINST taking the trade. Cites correlation with existing positions, drawdown risk, stop-loss distance vs upside, weak or missing levels.

3. NEUTRAL debator — synthesizes. Looks at risk:reward ratio, signal quality, position concentration.

4. PORTFOLIO MANAGER — final call. Outputs the JSON.

## Decision rules

This is a BINARY gate: every trade taken is the full fixed $60 risk — there is no trimming or partial sizing. You either APPROVE the full $60 or REJECT.

- REJECT if: there is no stop loss (risk would be unbounded); or, when take-profits are given, risk:reward is worse than 1:1.5; or the signal directly contradicts a sound existing position; or the setup is too weak or vague to deserve a full $60 commitment (e.g. no defined entry logic, no targets AND an unusually wide stop, or passive/non-actionable wording).
- APPROVE if: the setup is sound enough to justify the full $60 — take it.
- Do NOT reject for portfolio concentration, position count, or total open risk — aggregate exposure is context, NOT a blocker. There is no bankroll-percentage ceiling.

## Output format

Output ONLY a JSON object on a single line, no other text, no markdown fences:

{{"verdict":"APPROVE|REJECT","confidence":0-100,"suggested_risk_pct":FLOAT,"reasoning":"one paragraph","debate_summary":"AGG: ... | CON: ... | NEU: ..."}}

Constraints:
- `verdict` must be one of: APPROVE, REJECT
- `suggested_risk_pct`: APPROVE -> 20 (the full standard $60); REJECT -> 0. There is no in-between — never suggest a partial size.
- `reasoning`: 2-4 sentences, plain prose, no markdown
- `debate_summary`: pipe-separated, ~20 words per voice
"""


def _load_dr_profit_context(max_chars: int = 2000) -> str:
    """Read the Dr. Profit performance analysis for historical grounding."""
    if not DR_PROFIT_PERF.exists():
        return "(No Dr. Profit performance file found in vault.)"
    try:
        text = DR_PROFIT_PERF.read_text(encoding="utf-8")
        return text[:max_chars] + ("..." if len(text) > max_chars else "")
    except Exception as e:
        return f"(Could not read performance file: {e})"


# Sources that are NOT real exposure — seeded Dr. Profit history + explicit
# backfills. The risk gate must never count these (or any paper/dry-run row)
# toward open risk, or it rejects live signals against imaginary positions.
_NON_REAL_SOURCES = {"dr_profit_history", "backfill"}


def _real_positions_only(trades: list[dict]) -> list[dict]:
    """Keep only REAL open exposure: drop paper/dry-run rows and seeded/backfilled
    historical rows so they don't inflate the open-risk math Opus reasons over."""
    real = []
    for t in trades:
        ex = t.get("extra") or {}
        if ex.get("paper") is True:
            continue
        if str(t.get("source", "")).lower() in _NON_REAL_SOURCES:
            continue
        real.append(t)
    return real


def _format_active_trades(active: list[dict]) -> str:
    """Split the executor's open book into live POSITIONS vs resting (unfilled)
    LIMIT orders so the risk gate reasons about each distinctly — a resting limit
    is not real exposure yet, but it IS pending commitment at a price level.
    Records logged before order-state tracking existed fall back to POSITION
    (their fill state is unknowable). Reads classification from t["extra"]."""
    if not active:
        return "(No open positions or resting limit orders.)"

    positions, resting = [], []
    for t in active:
        ex = t.get("extra") or {}
        state = ex.get("order_state")
        is_resting = state == "resting" or (
            state is None
            and str(ex.get("order_type", "")).upper() == "LIMIT"
            and not ex.get("filled_units")
        )
        (resting if is_resting else positions).append(t)

    def _line(t: dict) -> str:
        lev = t.get("leverage")
        lev_str = f", {int(lev)}x" if lev else ""
        stop = t.get("stop_loss")
        stop_str = f", stop ${stop:,.2f}" if stop else ", NO STOP"
        pnl = t.get("pnl")
        pnl_str = f", PnL ${pnl:+.2f}" if pnl is not None else ", PnL pending"
        return (f"  - {t['asset']} {t['direction']} @ ${t.get('entry_price', 0):,.2f} "
                f"(risk ${t.get('risk_usd', 0):.2f}{lev_str}{stop_str}{pnl_str})")

    out = ["OPEN POSITIONS (live exposure):"]
    out += ([_line(t) for t in positions] or ["  (none)"])
    out += ["", "RESTING LIMIT ORDERS (placed, awaiting fill — not yet exposure):"]
    out += ([_line(t) for t in resting] or ["  (none)"])
    return "\n".join(out)


def _safe_verdict(reason: str) -> dict:
    """Sentinel verdict when the gate fails — never blocks the alert."""
    return {
        "verdict":            "UNKNOWN",
        "confidence":         0,
        "suggested_risk_pct": 0.0,
        "reasoning":          f"Risk gate unavailable: {reason}. Proceed using your own judgment.",
        "debate_summary":     "",
    }


def _extract_json(text: str) -> dict | None:
    """Find the first JSON object in the LLM output. Tolerates leading prose."""
    if not text:
        return None
    # Strip code fences if present
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    # Find the first {...} block
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def evaluate_signal(sig: dict, broadcast=None) -> dict:
    """
    Run the risk gate on a parsed Dr. Profit signal.
    Returns a verdict dict. NEVER raises — returns sentinel on failure.
    """
    try:
        from tools.llm import run_llm_command
        from tools.trade_tracker import get_bankroll, get_active_trades
    except ImportError as e:
        log.warning("[risk_gate] Import failed: %s", e)
        return _safe_verdict("internal import error")

    try:
        br = get_bankroll()
        active = _real_positions_only(get_active_trades())
    except Exception as e:
        log.warning("[risk_gate] Bankroll/trade read failed: %s", e)
        return _safe_verdict("bankroll read error")

    prompt = _RISK_GATE_PROMPT.format(
        asset        = sig["asset"],
        direction    = sig["direction"],
        entry        = sig["entry"],
        stop_loss    = f"${sig['stop_loss']:,.2f}" if sig.get("stop_loss") else "not specified",
        take_profits = sig.get("take_profit") or "not specified",
        leverage     = sig.get("leverage", 1),
        raw          = sig.get("raw", "")[:400],
        bankroll     = br["bankroll"],
        starting     = br["starting"],
        realized_pnl = br["realized_pnl"],
        open_pnl     = sum((t.get("pnl") or 0) for t in active),
        win_rate     = br["win_rate"],
        wins         = br["wins"],
        losses       = br["losses"],
        open_trades  = len(active),
        active_trades_block = _format_active_trades(active),
        dr_profit_context   = _load_dr_profit_context(),
    )

    if broadcast:
        try:
            await broadcast({"type": "risk_gate_activity",
                             "text": f"Evaluating {sig['asset']} {sig['direction']} signal..."})
        except Exception:
            pass

    try:
        res = await run_llm_command(
            prompt=prompt,
            broadcast=broadcast,
            allowed_tools="",  # pure reasoning — no tools needed
            agent_name="risk_gate",
        )
    except Exception as e:
        log.warning("[risk_gate] LLM call raised: %s", e)
        return _safe_verdict("LLM call failed")

    raw_out = res.get("result", "") if isinstance(res, dict) else str(res)
    verdict = _extract_json(raw_out)

    if not verdict or "verdict" not in verdict:
        log.warning("[risk_gate] Could not parse JSON from output: %s", raw_out[:300])
        return _safe_verdict("could not parse verdict JSON")

    # Normalize and clamp
    v = str(verdict.get("verdict", "")).upper()
    if v not in {"APPROVE", "RESIZE", "REJECT"}:
        v = "UNKNOWN"

    try:
        conf = float(verdict.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(100.0, conf))

    try:
        risk_pct = float(verdict.get("suggested_risk_pct", 0))
    except (TypeError, ValueError):
        risk_pct = 0.0
    risk_pct = max(0.0, min(20.0, risk_pct))

    return {
        "verdict":            v,
        "confidence":         conf,
        "suggested_risk_pct": risk_pct,
        "reasoning":          str(verdict.get("reasoning", "")).strip(),
        "debate_summary":     str(verdict.get("debate_summary", "")).strip(),
    }


def format_verdict_block(v: dict) -> str:
    """Render the verdict for the Telegram alert."""
    if v["verdict"] == "UNKNOWN":
        return "\n─ RISK GATE ─\n(unavailable — " + v["reasoning"] + ")"

    icon = {"APPROVE": "✅", "RESIZE": "⚠️", "REJECT": "🛑"}.get(v["verdict"], "❓")
    lines = [
        "",
        "─ RISK GATE ─",
        f"{icon} {v['verdict']}  (confidence {v['confidence']:.0f}%)",
    ]
    if v["verdict"] == "RESIZE":
        lines.append(f"Suggested risk: {v['suggested_risk_pct']:.1f}% of bankroll  (default is 20%)")
    elif v["verdict"] == "REJECT":
        lines.append("Recommend skipping this signal.")
    if v["reasoning"]:
        lines += ["", v["reasoning"]]
    if v["debate_summary"]:
        lines += ["", v["debate_summary"]]
    return "\n".join(lines)
