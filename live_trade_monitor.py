"""
Live trade monitor — 10-minute poll over active Dr. Profit trades.

Every cycle it:
  1. fetches live prices, marks each active trade to market, persists open PnL,
  2. evaluates each trade against its exit_plan + recent price, and
  3. Telegram-messages Taran a compact PnL snapshot plus any ADJUSTMENT actions
     (fill at best entry, bank TP1 + move stop to breakeven, trail the runner,
     approaching / past stop).

State (which one-time actions already fired) lives in monitor_state.json so we
don't re-nag the same condition every cycle and so we never race the dashboard's
own pnl writes on trades.json.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

from tools.market_prices import get_prices
from tools.trade_tracker import get_active_trades, get_bankroll, update_trade_pnl

log = logging.getLogger(__name__)

POLL_SECS    = int(os.environ.get("PAIS_MONITOR_POLL_SECS", "600"))  # 10 min
# Alerts-only: still refresh + persist price/PnL every cycle, but only Telegram
# when a trade actually needs adjusting. Set PAIS_MONITOR_ALERTS_ONLY=0 for a
# snapshot every cycle.
ALERTS_ONLY  = os.environ.get("PAIS_MONITOR_ALERTS_ONLY", "1") != "0"
NEAR_STOP_R  = 0.25   # "about to hit stop" = within 0.25R of the stop
NEAR_TP_PCT  = float(os.environ.get("PAIS_MONITOR_NEAR_TP_PCT", "0.02"))  # "almost at TP" = within 2% of target
TRAIL_STEP_R = 0.5    # (unused) kept for compatibility
STATE_FILE   = Path(__file__).parent / "monitor_state.json"


# ── de-dup state ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning("[monitor] state save failed: %s", e)


# ── telegram ─────────────────────────────────────────────────────────────────

async def _send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("[monitor] telegram creds missing")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(url, json={"chat_id": int(chat), "text": text[:4000],
                                    "disable_web_page_preview": True})
    except Exception as e:
        log.warning("[monitor] telegram send failed: %s", e)


# ── evaluation (pure) ────────────────────────────────────────────────────────

def _r_now(trade: dict, mark: float) -> float | None:
    entry = trade.get("entry_price")
    stop  = trade.get("initial_stop") or trade.get("stop_loss")
    if not entry or not stop:
        return None
    dist = abs(entry - stop)
    if dist <= 0:
        return None
    sign = 1 if trade.get("direction") == "LONG" else -1
    return (mark - entry) / dist * sign


def evaluate(trade: dict, mark: float, st: dict) -> tuple[list[str], dict]:
    """
    Alert ONLY on the three things Taran asked for:
      1. a pending order FILLS (its limit price is crossed),
      2. price is ABOUT TO HIT / has hit the stop-loss,
      3. price is ALMOST AT / has hit the take-profit.
    Everything else stays silent. De-dup flags in `st` stop re-nagging; they
    reset once price leaves the trigger zone so a later approach can re-fire.
    """
    actions: list[str] = []
    st = dict(st or {})
    asset = trade.get("asset", "?")
    direction = trade.get("direction", "")
    is_long = direction == "LONG"
    entry = trade.get("entry_price")
    stop  = trade.get("initial_stop") or trade.get("stop_loss")
    tps   = trade.get("take_profit") or []
    tp    = tps[0] if tps else None

    # 1) FILL — resting limit order whose price has been reached
    if trade.get("status") == "waiting_entry":
        best = (trade.get("extra") or {}).get("best_entry") or entry
        if best:
            fillable = (is_long and mark <= best) or (not is_long and mark >= best)
            if fillable and not st.get("fill_alerted"):
                actions.append(f"✅ {asset} {direction} FILLED at {best:g} (mark {mark:g}).")
                st["fill_alerted"] = True
        return actions, st

    # 2) STOP — about to hit / hit
    if entry and stop:
        dist = abs(entry - stop)
        if dist > 0:
            r = (mark - entry) / dist * (1 if is_long else -1)
            if r <= -1.0:
                if not st.get("stop_hit"):
                    actions.append(f"🛑 {asset} {direction}: STOP {stop:g} HIT (mark {mark:g}). Close it.")
                    st["stop_hit"] = True
            elif r <= -1.0 + NEAR_STOP_R:
                if not st.get("near_stop"):
                    actions.append(f"⚠️ {asset} {direction}: about to hit STOP {stop:g} "
                                   f"(mark {mark:g}, {(-1.0 - r):.2f}R away).")
                    st["near_stop"] = True
            else:
                st.pop("near_stop", None); st.pop("stop_hit", None)

    # 3) TARGET — almost at / hit
    if entry and tp:
        in_profit = (is_long and mark > entry) or (not is_long and mark < entry)
        hit  = (is_long and mark >= tp) or (not is_long and mark <= tp)
        near = abs(mark - tp) / tp <= NEAR_TP_PCT
        if hit:
            if not st.get("tp_hit"):
                actions.append(f"🎯 {asset} {direction}: TARGET {tp:g} HIT (mark {mark:g})! Take profit.")
                st["tp_hit"] = True
        elif near and in_profit:
            if not st.get("near_tp"):
                actions.append(f"🎯 {asset} {direction}: almost at TARGET {tp:g} "
                               f"(mark {mark:g}, within {NEAR_TP_PCT*100:g}%).")
                st["near_tp"] = True
        else:
            st.pop("near_tp", None); st.pop("tp_hit", None)

    return actions, st


# ── poll ─────────────────────────────────────────────────────────────────────

async def poll_once() -> dict:
    active = get_active_trades()
    if not active:
        return {"active": 0, "sent": False}

    prices = await get_prices(
        [(t["asset"], t.get("asset_class", "crypto")) for t in active])

    state = _load_state()
    action_lines: list[str] = []

    for t in active:
        mark = prices.get(t["asset"])
        if not mark:
            continue

        # mark-to-market + persist so the desk's open PnL stays current
        if t.get("entry_price") and t.get("position_size"):
            sign = 1 if t["direction"] == "LONG" else -1
            pnl = round((mark - t["entry_price"]) * t["position_size"] * sign, 2)
            try:
                update_trade_pnl(t["id"], pnl, exit_price=None)
            except Exception as e:
                log.warning("[monitor] pnl persist failed for %s: %s", t["id"], e)

        acts, new_st = evaluate(t, mark, state.get(t["id"], {}))
        if acts:
            action_lines += acts
        state[t["id"]] = new_st

    # prune state for trades no longer active
    live_ids = {t["id"] for t in active}
    state = {k: v for k, v in state.items() if k in live_ids}
    _save_state(state)

    # Only ping on a real event — a fill, an approaching/hit stop, or a near/hit target.
    if action_lines:
        await _send_telegram("\n".join(action_lines))
        return {"active": len(active), "sent": True, "actions": len(action_lines)}
    return {"active": len(active), "sent": False, "actions": 0}


async def run_monitor(interval_secs: int = POLL_SECS) -> None:
    log.info("[monitor] started — polling every %ds", interval_secs)
    await asyncio.sleep(20)   # let the server settle before the first poll
    while True:
        try:
            res = await poll_once()
            if res.get("sent"):
                log.info("[monitor] polled %d active, %d actions",
                         res["active"], res.get("actions", 0))
        except Exception as e:
            log.exception("[monitor] poll error: %s", e)
        await asyncio.sleep(interval_secs)
