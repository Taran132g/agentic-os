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

2. CONSERVATIVE debator — argues AGAINST or for SMALLER size. Cites bankroll preservation, correlation with existing positions, drawdown risk, stop-loss distance vs upside.

3. NEUTRAL debator — synthesizes. Looks at risk:reward ratio, signal quality, position concentration.

4. PORTFOLIO MANAGER — final call. Outputs the JSON.

## Decision rules

- REJECT if: no stop loss AND signal direction conflicts with existing open position; or risk:reward < 1:1.5; or new trade would push total open risk > 40% of bankroll.
- RESIZE if: signal is directionally sound but proposed 20% bankroll risk is too high for current conditions (suggest a lower risk_pct, 5-15%).
- APPROVE if: setup is clean, R:R >= 1:2, doesn't over-concentrate, bankroll can absorb full 20% risk.

## Output format

Output ONLY a JSON object on a single line, no other text, no markdown fences:

{{"verdict":"APPROVE|RESIZE|REJECT","confidence":0-100,"suggested_risk_pct":FLOAT,"reasoning":"one paragraph","debate_summary":"AGG: ... | CON: ... | NEU: ..."}}

Constraints:
- `verdict` must be one of: APPROVE, RESIZE, REJECT
- `suggested_risk_pct`: if APPROVE, equals 20; if RESIZE, between 5 and 15; if REJECT, 0
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


def _format_active_trades(active: list[dict]) -> str:
    if not active:
        return "(No active trades.)"
    lines = []
    for t in active:
        pnl = t.get("pnl")
        pnl_str = f"PnL ${pnl:+.2f}" if pnl is not None else "PnL pending"
        lines.append(
            f"- {t['asset']} {t['direction']} @ ${t['entry_price']:,.2f}  "
            f"(risk ${t['risk_usd']:.2f}, {pnl_str})"
        )
    return "\n".join(lines)


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
        active = get_active_trades()
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
        open_pnl     = br["open_pnl"],
        win_rate     = br["win_rate"],
        wins         = br["wins"],
        losses       = br["losses"],
        open_trades  = br["open_trades"],
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
