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
NEAR_STOP_R  = 0.25   # warn when within 0.25R of the stop
TRAIL_STEP_R = 0.5    # only re-suggest a trail move after +0.5R of new progress
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
    Return (action_lines, new_state) for one trade. `st` is this trade's prior
    monitor state; the returned state carries de-dup flags forward.
    """
    actions: list[str] = []
    st = dict(st or {})
    asset = trade.get("asset", "?")
    direction = trade.get("direction", "")
    is_long = direction == "LONG"
    entry = trade.get("entry_price")
    stop  = trade.get("initial_stop") or trade.get("stop_loss")
    plan  = trade.get("exit_plan") or {}
    dist  = abs(entry - stop) if (entry and stop) else None

    # --- pending order: nudge toward the best entry ---
    if trade.get("status") == "waiting_entry":
        best = (trade.get("extra") or {}).get("best_entry") or entry
        if best:
            gap = (mark - best) / best * 100
            fillable = (is_long and mark <= best) or (not is_long and mark >= best)
            if fillable and not st.get("fill_alerted"):
                actions.append(f"🎯 {asset}: price {mark:g} reached best entry {best:g} — "
                               f"place the {direction} now.")
                st["fill_alerted"] = True
            elif not fillable:
                actions.append(f"⏳ {asset}: waiting for {best:g} to {direction} "
                               f"(now {mark:g}, {gap:+.1f}%).")
        return actions, st

    r = _r_now(trade, mark)
    if r is None or dist is None:
        return actions, st

    tp1_r   = (plan.get("tp1") or {}).get("r", 2.0)
    trail_r = (plan.get("runner") or {}).get("trail_r", 1.0)

    # --- past the stop → close ---
    if r <= -1.0:
        if not st.get("stopped_alerted"):
            actions.append(f"🛑 {asset}: hit stop ({mark:g}, {r:+.2f}R) — close it, that's -1R.")
            st["stopped_alerted"] = True
        return actions, st

    # --- approaching stop ---
    if r <= -1.0 + NEAR_STOP_R and not st.get("near_stop_alerted"):
        actions.append(f"⚠️ {asset}: {r:+.2f}R, within {NEAR_STOP_R:g}R of stop {stop:g} — watch it.")
        st["near_stop_alerted"] = True

    # --- TP1 reached: bank partial + move stop to breakeven (one time) ---
    if r >= tp1_r and not st.get("be_moved"):
        frac = int((plan.get("tp1") or {}).get("close_frac", 0.34) * 100)
        actions.append(f"✅ {asset}: TP1 hit ({mark:g}, +{r:.2f}R) — bank ~{frac}% and "
                       f"move stop to breakeven {entry:g}.")
        st["be_moved"] = True
        st["last_trail_r"] = r

    # --- runner: suggest trailing the stop as new progress accrues ---
    if st.get("be_moved") and r >= tp1_r:
        last = st.get("last_trail_r", tp1_r)
        if r >= last + TRAIL_STEP_R:
            new_stop = mark - (1 if is_long else -1) * trail_r * dist
            locked = r - trail_r
            actions.append(f"🔵 {asset}: {r:+.2f}R — trail stop to {new_stop:g} "
                           f"(locks ~+{locked:.2f}R).")
            st["last_trail_r"] = r

    return actions, st


# ── poll ─────────────────────────────────────────────────────────────────────

async def poll_once() -> dict:
    active = get_active_trades()
    if not active:
        return {"active": 0, "sent": False}

    prices = await get_prices(
        [(t["asset"], t.get("asset_class", "crypto")) for t in active])

    state = _load_state()
    snap_lines, action_lines, total_open = [], [], 0.0

    for t in active:
        mark = prices.get(t["asset"])
        if not mark:
            snap_lines.append(f"• {t['asset']} {t.get('direction','')}: no price")
            continue

        # mark-to-market + persist (same math as /api/live/prices)
        pnl = None
        if t.get("entry_price") and t.get("position_size"):
            sign = 1 if t["direction"] == "LONG" else -1
            pnl = round((mark - t["entry_price"]) * t["position_size"] * sign, 2)
            total_open += pnl
            try:
                update_trade_pnl(t["id"], pnl, exit_price=None)
            except Exception as e:
                log.warning("[monitor] pnl persist failed for %s: %s", t["id"], e)

        r = _r_now(t, mark)
        rtxt = f"{r:+.2f}R" if r is not None else "—"
        pnltxt = f"${pnl:+.2f}" if pnl is not None else "pending"
        tag = "⏳" if t.get("status") == "waiting_entry" else ""
        snap_lines.append(f"• {t['asset']} {t.get('direction','')}{tag}: "
                          f"{mark:g}  {pnltxt}  {rtxt}")

        acts, new_st = evaluate(t, mark, state.get(t["id"], {}))
        if acts:
            action_lines += acts
        state[t["id"]] = new_st

    # prune state for trades no longer active
    live_ids = {t["id"] for t in active}
    state = {k: v for k, v in state.items() if k in live_ids}
    _save_state(state)

    bank = get_bankroll()
    header = (f"📊 Trade monitor — open PnL ${total_open:+.2f} | "
              f"bankroll ${bank['bankroll']:.2f}")
    msg = header + "\n" + "\n".join(snap_lines)
    if action_lines:
        msg += "\n\n⚠️ ADJUST:\n" + "\n".join(action_lines)

    # Alerts-only: stay silent on quiet cycles (price/PnL were still persisted).
    if action_lines or not ALERTS_ONLY:
        await _send_telegram(msg)
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
