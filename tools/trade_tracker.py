"""
Trade tracker — bankroll management and active trade log.

Persistent store: ~/agentic_os/trades.json
Bankroll starts at $1,000. Each closed trade updates it.
Taran updates PnL via the /trades dashboard; tracker recalculates totals.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

TRADES_FILE = Path(__file__).parent.parent / "trades.json"
STARTING_BANKROLL = 1000.0


# ── Data schema ───────────────────────────────────────────────────────────────
#
# {
#   "bankroll": 1000.0,             # current realized bankroll
#   "starting_bankroll": 1000.0,
#   "active_trades": [...],
#   "closed_trades": [...],
# }
#
# Trade:
# {
#   "id": "uuid8",
#   "asset": "BTC",
#   "direction": "LONG",           # LONG | SHORT
#   "entry_price": 65000.0,
#   "stop_loss": 64000.0,
#   "take_profit": [67000.0, 69000.0],
#   "position_size": 0.00015,      # units of the asset
#   "notional": 9.75,              # USD value at entry
#   "risk_usd": 9.75,              # max risk in USD
#   "risk_pct": 1.0,               # % of bankroll risked
#   "leverage": 1,
#   "pnl": null,                   # null while open, float when closed
#   "exit_price": null,
#   "status": "active",            # active | closed | cancelled
#   "source": "dr_profit",
#   "signal_text": "...",          # original signal snippet
#   "opened_at": "ISO",
#   "closed_at": null,
#   "notes": "",
# }


def _load() -> dict:
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text())
        except Exception:
            pass
    return {
        "bankroll": STARTING_BANKROLL,
        "starting_bankroll": STARTING_BANKROLL,
        "active_trades": [],
        "closed_trades": [],
    }


def _save(data: dict):
    TRADES_FILE.write_text(json.dumps(data, indent=2))


# ── Read ──────────────────────────────────────────────────────────────────────

def get_bankroll() -> dict:
    """Return bankroll summary: current, starting, realized PnL, open PnL, win rate."""
    data = _load()
    closed = data["closed_trades"]
    active = data["active_trades"]

    realized_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
    open_pnl = sum(t.get("pnl", 0) or 0 for t in active if t.get("pnl") is not None)

    # Cancelled trades live in closed_trades but were never filled — exclude
    # them from win/loss stats so they don't drag the win rate toward zero.
    settled = [t for t in closed if t.get("status") != "cancelled"]
    wins = [t for t in settled if (t.get("pnl") or 0) > 0]
    losses = [t for t in settled if (t.get("pnl") or 0) <= 0]

    return {
        "bankroll":        data["bankroll"],
        "starting":        data["starting_bankroll"],
        "realized_pnl":    round(realized_pnl, 2),
        "open_pnl":        round(open_pnl, 2),
        "total_pnl":       round(realized_pnl + open_pnl, 2),
        "win_rate":        round(len(wins) / max(len(settled), 1) * 100, 1),
        "wins":            len(wins),
        "losses":          len(losses),
        "open_trades":     len(active),
        "closed_trades":   len(settled),
    }


def get_active_trades() -> list[dict]:
    return _load()["active_trades"]


def get_closed_trades(limit: int = 50) -> list[dict]:
    return _load()["closed_trades"][-limit:]


def get_all_trades() -> dict:
    data = _load()
    return {
        "bankroll_summary": get_bankroll(),
        "active":  data["active_trades"],
        "closed":  data["closed_trades"][-50:],
    }


# ── Write ─────────────────────────────────────────────────────────────────────

def add_trade(
    asset: str,
    direction: str,
    entry_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[list[float]],
    risk_pct: float = 1.0,
    leverage: int = 1,
    signal_text: str = "",
    source: str = "dr_profit",
    status: str = "active",
    risk_usd: Optional[float] = None,
    asset_class: str = "crypto",
    extra: Optional[dict] = None,
    units: Optional[float] = None,
) -> dict:
    """
    Add a trade. status='waiting_entry' for pending signals, 'active' once filled.
    risk_usd (fixed-dollar risk, e.g. live_signal's $60) overrides risk_pct;
    when given, risk_pct is back-computed for display.
    """
    data = _load()
    bankroll = data["bankroll"]

    if risk_usd is not None:
        risk_usd = round(risk_usd, 2)
        risk_pct = round(risk_usd / bankroll * 100, 2) if bankroll > 0 else 0.0
    else:
        risk_usd = round(bankroll * risk_pct / 100, 2)

    # Position sizing: if SL known, size = risk / (entry - sl) * leverage
    position_size = 0.0
    notional = 0.0
    if units is not None:
        # Caller (position_sizer) already computed exact units: loss at SL
        # equals risk_usd regardless of leverage. Leverage only affects margin.
        position_size = round(units, 8)
        notional = round(units * entry_price, 2)
    elif stop_loss and entry_price:
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance > 0:
            # Units we can buy so that if SL is hit, we lose exactly risk_usd
            position_size = round((risk_usd * leverage) / sl_distance, 8)
            notional = round(position_size * entry_price / leverage, 2)
    elif entry_price:
        # No SL: default to risk_usd notional at 2% assumed stop
        assumed_stop_pct = 0.02
        position_size = round((risk_usd / (entry_price * assumed_stop_pct)), 8)
        notional = round(risk_usd / assumed_stop_pct, 2)

    trade: dict = {
        "id":           str(uuid.uuid4())[:8],
        "asset":        asset.upper(),
        "asset_class":  asset_class,
        "direction":    direction.upper(),
        "entry_price":  entry_price,
        "stop_loss":    stop_loss,
        "take_profit":  take_profit or [],
        "position_size": position_size,
        "notional":     notional,
        "risk_usd":     risk_usd,
        "risk_pct":     risk_pct,
        "leverage":     leverage,
        "pnl":          None,
        "exit_price":   None,
        "status":       status,
        "source":       source,
        "signal_text":  signal_text[:500],
        "opened_at":    datetime.now().isoformat(),
        "closed_at":    None,
        "notes":        "",
    }
    if extra:
        trade["extra"] = extra

    data["active_trades"].append(trade)
    _save(data)
    return trade


def update_trade_pnl(trade_id: str, pnl: float, exit_price: Optional[float] = None,
                     notes: str = "") -> Optional[dict]:
    """Update PnL on an active trade (called from dashboard). Does NOT close it yet."""
    data = _load()
    for t in data["active_trades"]:
        if t["id"] == trade_id:
            t["pnl"] = round(pnl, 2)
            if exit_price:
                t["exit_price"] = exit_price
            if notes:
                t["notes"] = notes
            _save(data)
            return t
    return None


def close_trade(trade_id: str, pnl: float, exit_price: Optional[float] = None,
                notes: str = "") -> Optional[dict]:
    """Close a trade, move to closed_trades, update bankroll."""
    data = _load()
    trade = None
    remaining = []
    for t in data["active_trades"]:
        if t["id"] == trade_id:
            trade = t
        else:
            remaining.append(t)

    if not trade:
        return None

    trade["pnl"]       = round(pnl, 2)
    trade["exit_price"] = exit_price
    trade["status"]    = "closed"
    trade["closed_at"] = datetime.now().isoformat()
    if notes:
        trade["notes"] = notes

    data["active_trades"]  = remaining
    data["closed_trades"].append(trade)
    data["bankroll"]       = round(data["bankroll"] + pnl, 2)
    _save(data)
    return trade


def cancel_trade(trade_id: str) -> bool:
    data = _load()
    remaining = []
    found = False
    for t in data["active_trades"]:
        if t["id"] == trade_id:
            t["status"] = "cancelled"
            t["closed_at"] = datetime.now().isoformat()
            data["closed_trades"].append(t)
            found = True
        else:
            remaining.append(t)
    if found:
        data["active_trades"] = remaining
        _save(data)
    return found


def reset_bankroll(amount: float = STARTING_BANKROLL):
    data = _load()
    data["bankroll"] = amount
    data["starting_bankroll"] = amount
    _save(data)


# ── Signal sizing helper (used by dr_profit_monitor) ─────────────────────────

def calculate_sizes(entry: float, stop_loss: Optional[float],
                    asset: str, leverage: int = 1) -> dict:
    """
    Return position size for 20% bankroll risk based on stop loss distance.
    Used to format the Telegram alert.
    """
    data = _load()
    bankroll = data["bankroll"]

    sl_distance = abs(entry - stop_loss) if stop_loss else entry * 0.02
    risk_usd = bankroll * 20 / 100
    units = round((risk_usd * leverage) / sl_distance, 6)
    notional = round(units * entry / leverage, 2)
    margin = round(notional / leverage, 2) if leverage > 1 else None

    return {
        "asset":     asset.upper(),
        "entry":     entry,
        "stop_loss": stop_loss,
        "bankroll":  bankroll,
        "risk_usd":  round(risk_usd, 2),
        "risk_pct":  20,
        "units":     units,
        "notional":  notional,
        "leverage":  leverage,
        "margin":    margin,
    }
