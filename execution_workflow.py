"""
Execution workflow — the auto-executor that turns a Dr. Profit signal into a
real (or simulated) order on Yubit USDT-M perps.

Pipeline (all fail-safe — never raises to the monitor):
    parse (Claude agent) -> size ($-risk, deterministic) -> risk gate ->
    pre-trade caps -> place order (paper | testnet | live) -> record -> alert-block

Safety is layered and every layer can only make the trade SMALLER or skip it:
  * global gate     — kill switch / auto-execute off  (tools/exec_config.py)
  * dedupe          — identical signal inside a window is ignored
  * confidence gate — agent must be >= EXEC_MIN_CONFIDENCE
  * asset allowlist — optional EXEC_ALLOWED_ASSETS
  * risk gate       — a REJECT verdict blocks (EXEC_RISK_GATE_BLOCKS)
  * leverage cap    — clamped to EXEC_MAX_LEVERAGE
  * notional cap    — position clamped DOWN to EXEC_MAX_NOTIONAL_USD
  * concurrency cap — EXEC_MAX_CONCURRENT open auto positions
  * daily caps      — EXEC_MAX_TRADES_PER_DAY, EXEC_DAILY_LOSS_STOP_USD

Real orders fire only when EXECUTION_MODE=testnet|live AND auto-execute is on
AND the kill switch is off AND keys are present. Default config places nothing.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

AGENTIC_DIR = Path(__file__).parent
STATE_FILE  = AGENTIC_DIR / "execution_state.json"
DEDUP_WINDOW_SECS = int(os.environ.get("EXEC_DEDUP_WINDOW_SECS", "21600") or "21600")  # 6h

AUTO_SOURCE  = "dr_profit_auto"    # tracker source tag for real (testnet/live) auto trades
PAPER_SOURCE = "dr_profit_paper"   # tracker source tag for dry-run paper trades


# ── persistent execution state (dedupe + daily counters) ─────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"executions": []}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning("[exec] could not persist state: %s", e)


def _signal_hash(source: str, sig: dict) -> str:
    key = "|".join([
        source,
        str(sig.get("asset", "")).upper(),
        str(sig.get("direction", "")).upper(),
        f"{float(sig.get('entry') or 0):.4f}",
        ",".join(f"{float(t):.4f}" for t in (sig.get("take_profits") or [])),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _recent_duplicate(state: dict, sig_hash: str) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    for e in state.get("executions", []):
        if e.get("hash") == sig_hash:
            try:
                ts = datetime.fromisoformat(e["ts"]).timestamp()
            except (KeyError, ValueError):
                continue
            if now - ts < DEDUP_WINDOW_SECS:
                return True
    return False


def _today_exec_count(state: dict) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for e in state.get("executions", []) if str(e.get("ts", "")).startswith(today))


def _today_auto_realized_loss() -> float:
    """Sum of today's realized PnL on auto trades (negative = loss)."""
    try:
        from tools.trade_tracker import _load
        data = _load()
    except Exception:
        return 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    for t in data.get("closed_trades", []):
        if not str(t.get("source", "")).startswith("dr_profit"):
            continue
        if str(t.get("closed_at", "")).startswith(today) and t.get("pnl") is not None:
            total += float(t["pnl"])
    return total


def _open_auto_positions() -> int:
    try:
        from tools.trade_tracker import get_active_trades
        return sum(1 for t in get_active_trades()
                   if str(t.get("source", "")).startswith("dr_profit"))
    except Exception:
        return 0


# ── broker selection ─────────────────────────────────────────────────────────

def _pick_broker(cfg):
    from tools.broker import PaperBroker
    if cfg.is_dry_run:
        return PaperBroker(), None
    from tools.yubit_client import YubitBroker
    broker = YubitBroker(testnet=cfg.is_testnet)
    if not broker.configured():
        env = "TESTNET" if cfg.is_testnet else "LIVE"
        return None, f"Yubit {env} API key/secret not set — cannot place orders"
    return broker, None


def _skip(status: str, reason: str, **extra) -> dict:
    return {"executed": False, "status": status, "reason": reason, **extra}


# ── main entrypoint ──────────────────────────────────────────────────────────

async def execute_signal(text: str, source: str = "dr_profit", broadcast=None) -> dict:
    """
    Run the full auto-execution pipeline on raw signal text.
    Returns a structured result dict. NEVER raises.
    """
    try:
        return await _execute_signal_inner(text, source, broadcast)
    except Exception as e:                      # absolute backstop
        log.exception("[exec] pipeline crashed")
        return _skip("failed", f"executor crashed: {type(e).__name__}: {e}")


async def _execute_signal_inner(text: str, source: str, broadcast) -> dict:
    from tools.exec_config import load_config
    cfg = load_config()

    gate = cfg.gate_reason()
    if gate:
        return _skip("blocked", gate, mode=cfg.mode)

    # ── parse: tiered to spend the fewest credits ──
    # Stage 1 is a cheap Haiku gate ("is this even an actionable buy/sell?"); the
    # pricier Sonnet extraction (Stage 2) only runs on posts that pass it.
    from live_signal_workflow import (_classify_signal, _claude_parse,
                                      _normalize, _regex_fallback)
    from tools.trade_tracker import resolve_risk_usd
    risk_usd = resolve_risk_usd()

    # Stage 1 — cheap classify gate. On classifier failure (None) we fall through
    # to extraction rather than drop a possibly-real signal.
    clf = await _classify_signal(text, broadcast=broadcast)
    if clf is not None:
        if not clf.get("is_signal"):
            return _skip("skipped", "classifier: not an actionable trade signal", mode=cfg.mode)
        clf_conf = int(clf.get("confidence") or 0)
        if clf_conf < cfg.min_confidence:
            return _skip("skipped",
                         f"classifier confidence {clf_conf} < EXEC_MIN_CONFIDENCE {cfg.min_confidence}",
                         mode=cfg.mode)

    # Stage 2 — capable extraction, only for posts that cleared the cheap gate.
    parsed = await _claude_parse(text, risk_usd=risk_usd, broadcast=broadcast)
    sig = _normalize(parsed) if parsed else None
    if sig is None:
        sig = _regex_fallback(text)
    if sig is None:
        return _skip("skipped", "could not parse an actionable trade from the message",
                     mode=cfg.mode)

    conf = int(sig.get("confidence") or 0)
    if conf < cfg.min_confidence:
        return _skip("skipped",
                     f"agent confidence {conf} < EXEC_MIN_CONFIDENCE {cfg.min_confidence}",
                     mode=cfg.mode, sig=sig)

    if cfg.allowed_assets and sig["asset"] not in cfg.allowed_assets:
        return _skip("skipped", f"{sig['asset']} not in EXEC_ALLOWED_ASSETS",
                     mode=cfg.mode, sig=sig)

    # Yubit trades crypto only. Stock picks (e.g. $COIN) can't be filled there —
    # skip them for real orders, but let dry-run still paper-record them so the
    # trader desk shows what was signalled.
    if sig["asset_class"] == "stock" and not cfg.is_dry_run:
        return _skip("skipped", f"{sig['asset']} is a stock — Yubit is crypto-only",
                     mode=cfg.mode, sig=sig)

    # ── dedupe ──
    state = _load_state()
    sig_hash = _signal_hash(source, sig)
    if _recent_duplicate(state, sig_hash):
        return _skip("skipped", "duplicate of a signal already handled recently",
                     mode=cfg.mode, sig=sig)

    # ── daily caps (cheap, pre-sizing) ──
    if _today_exec_count(state) >= cfg.max_trades_per_day:
        return _skip("blocked", f"daily trade cap reached ({cfg.max_trades_per_day})",
                     mode=cfg.mode, sig=sig)
    todays_loss = _today_auto_realized_loss()
    if todays_loss <= -abs(cfg.daily_loss_stop_usd):
        return _skip("blocked",
                     f"daily loss stop hit (${todays_loss:.0f} <= -${cfg.daily_loss_stop_usd:.0f})",
                     mode=cfg.mode, sig=sig)
    if cfg.max_concurrent > 0 and _open_auto_positions() >= cfg.max_concurrent:
        return _skip("blocked", f"max concurrent auto positions open ({cfg.max_concurrent})",
                     mode=cfg.mode, sig=sig)

    # ── order intent: MARKET (enter now / he's already in) vs LIMIT (rest at a price) ──
    # If Dr. Profit says "place a limit at X" -> LIMIT at X. If he says "I bought at X"
    # or "buy now" -> MARKET: we follow him in at the CURRENT price, and size off the
    # live mark (not his fill), so the risk stays honest even if price has moved.
    entry_type = sig.get("entry_type") or ("limit" if sig.get("entry") is not None else "market")
    size_entry = None if entry_type == "market" else sig["entry"]

    # ── size (deterministic, $-risk) ──
    from tools.position_sizer import size_trade, best_entry_in_zone
    sizing = await size_trade(
        asset=sig["asset"], asset_class=sig["asset_class"], direction=sig["direction"],
        entry=size_entry, stop_loss=sig["stop_loss"], take_profits=sig["take_profits"],
        leverage=sig["leverage"], risk_usd=risk_usd,
    )
    if not sizing.get("ok"):
        return _skip("skipped", sizing.get("error", "sizing failed"), mode=cfg.mode, sig=sig)

    # ── risk gate (advisory verdict; can block or resize) ──
    verdict = await _run_risk_gate(sig, sizing, text, broadcast)
    if cfg.risk_gate_blocks and verdict.get("verdict") == "REJECT":
        return _skip("skipped", f"risk gate REJECT — {verdict.get('reasoning', '')}",
                     mode=cfg.mode, sig=sig, sizing=sizing, verdict=verdict)

    resize_factor = 1.0
    if verdict.get("verdict") == "RESIZE":
        pct = float(verdict.get("suggested_risk_pct") or 20) or 20
        resize_factor = max(0.1, min(1.0, pct / 20.0))

    # ── apply caps: leverage, notional, resize ──
    leverage = min(int(sizing["leverage"]), cfg.max_leverage)
    if entry_type == "market":
        entry_px = sizing["entry"]            # live mark — the actual expected fill
    else:
        entry_px = best_entry_in_zone(sig.get("entry_low"), sig.get("entry_high"),
                                      sig["direction"], fallback=sizing["entry"])
    units = float(sizing["units"]) * resize_factor
    notional = units * entry_px
    clamp_note = None
    if notional > cfg.max_notional_usd > 0:
        scale = cfg.max_notional_usd / notional
        units *= scale
        notional = units * entry_px
        clamp_note = f"clamped to ${cfg.max_notional_usd:.0f} notional cap"

    # ── place the order ──
    broker, berr = _pick_broker(cfg)
    if broker is None:
        return _skip("blocked", berr, mode=cfg.mode, sig=sig, sizing=sizing)

    from tools.broker import OrderRequest
    order_type = "MARKET" if entry_type == "market" else "LIMIT"
    req = OrderRequest(
        asset=sig["asset"], direction=sig["direction"], units=units, leverage=leverage,
        entry=entry_px, order_type=order_type, stop_loss=sizing["stop_loss"],
        take_profits=sig["take_profits"],
        tp1_fraction=(sizing.get("exit_plan") or {}).get("tp1_frac"),
        client_id=f"drp-{sig_hash}",
    )
    result = await broker.open_position(req)

    if not result.ok:
        return _skip("failed", result.error or "order rejected", mode=cfg.mode,
                     sig=sig, sizing=sizing, verdict=verdict, warnings=list(result.warnings))

    # ── record + persist dedupe state ──
    # Dry-run trades ARE written to the tracker so they surface on the trader desk,
    # but flagged paper=True (no real order was placed). testnet/live record as real.
    trade_id = _record_trade(sig, sizing, result, leverage, units,
                             risk_usd * resize_factor, text, verdict,
                             paper=cfg.is_dry_run)
    state.setdefault("executions", []).append({
        "hash": sig_hash, "ts": datetime.now(timezone.utc).isoformat(),
        "asset": sig["asset"], "direction": sig["direction"],
        "mode": cfg.mode, "order_id": result.order_id, "trade_id": trade_id,
    })
    _save_state(state)

    log.info("[exec] %s %s %s %s @ %s — %.6f units, order %s, protected=%s",
             cfg.mode, sig["asset"], sig["direction"], order_type,
             result.avg_fill_price, result.filled_units, result.order_id, result.protected)

    return {
        "executed": True, "status": "placed", "mode": cfg.mode,
        "sig": sig, "sizing": sizing, "verdict": verdict,
        "order": {
            "order_id": result.order_id, "fill": result.avg_fill_price,
            "units": result.filled_units, "leverage": leverage, "notional": round(notional, 2),
            "stop_order_id": result.stop_order_id, "tp_order_ids": list(result.tp_order_ids),
        },
        "protected": result.protected, "trade_id": trade_id,
        "clamp_note": clamp_note, "warnings": list(result.warnings),
    }


async def _run_risk_gate(sig: dict, sizing: dict, text: str, broadcast) -> dict:
    """Adapt the normalized signal to the risk gate's expected shape. Never raises."""
    try:
        from risk_gate_workflow import evaluate_signal
        gate_sig = {
            "asset": sig["asset"], "direction": sig["direction"],
            "entry": sizing["entry"], "stop_loss": sizing.get("stop_loss"),
            "take_profit": sig.get("take_profits") or [],
            "leverage": sig.get("leverage", 1), "raw": text[:400],
        }
        return await evaluate_signal(gate_sig, broadcast=broadcast)
    except Exception as e:
        log.warning("[exec] risk gate skipped: %s", e)
        return {"verdict": "UNKNOWN", "confidence": 0, "reasoning": str(e)}


def _record_trade(sig, sizing, result, leverage, units, risk_usd, text, verdict,
                  paper: bool = False) -> str | None:
    from tools.trade_tracker import add_trade
    try:
        trade = add_trade(
            asset=sig["asset"], direction=sig["direction"],
            entry_price=result.avg_fill_price or sizing["entry"],
            stop_loss=sizing["stop_loss"], take_profit=sig["take_profits"],
            leverage=leverage, signal_text=text,
            source=PAPER_SOURCE if paper else AUTO_SOURCE,
            risk_usd=risk_usd, asset_class=sig["asset_class"], units=units,
            exit_plan=sizing.get("exit_plan"),
            extra={
                "exec_mode": result.mode, "paper": paper,
                "order_id": result.order_id,
                "stop_order_id": result.stop_order_id, "tp_order_ids": list(result.tp_order_ids),
                "protected": result.protected, "confidence": sig.get("confidence"),
                "agent_note": sig.get("note"), "risk_verdict": verdict.get("verdict"),
            },
        )
        return trade.get("id")
    except Exception as e:
        log.warning("[exec] tracker record failed (order still placed): %s", e)
        return None


# ── Telegram alert block ─────────────────────────────────────────────────────

def format_exec_block(res: dict) -> str:
    """Render the execution outcome for the Telegram alert appended to the signal."""
    status = res.get("status")
    mode = res.get("mode", "?")

    if status in ("blocked", "skipped"):
        icon = "⏸️" if status == "blocked" else "⏭️"
        return f"\n─ EXECUTION ─\n{icon} not placed — {res.get('reason', '')}"
    if status == "failed":
        w = "\n".join(f"  • {x}" for x in res.get("warnings", []))
        return f"\n─ EXECUTION ─\n❌ FAILED — {res.get('reason', '')}" + (f"\n{w}" if w else "")

    o = res.get("order", {})
    tag = {"dry_run": "DRY-RUN (nothing placed)", "testnet": "TESTNET",
           "live": "LIVE"}.get(mode, mode)
    prot = "🛡️ stop-loss set" if res.get("protected") else "⚠️ NO STOP-LOSS ON EXCHANGE"
    lines = [
        "",
        "─ EXECUTION ─",
        f"✅ {tag}  ·  {prot}",
        f"Order:  {o.get('units')} units @ ${(o.get('fill') or 0):,.4f}  "
        f"({o.get('leverage')}x, ≈${o.get('notional', 0):,.0f})",
    ]
    if o.get("order_id"):
        lines.append(f"ID:     {o.get('order_id')}")
    if res.get("clamp_note"):
        lines.append(f"Note:   {res['clamp_note']}")
    for w in res.get("warnings", []):
        lines.append(f"  • {w}")
    return "\n".join(lines)
