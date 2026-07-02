"""
Fixed-dollar-risk position sizer for Dr. Profit signals (crypto + stocks).

Every trade risks the same fixed USD amount (default $60, override with
PAIS_TRADE_RISK_USD). Size is normalized by stop distance:

    units = RISK_USD / |entry - stop|

When the signal has no stop loss, one is derived from volatility so the
"equal risk" is honest: the stop sits STOP_K sigma away over the expected
hold, where sigma comes from implied vol (BTC/ETH via Deribit DVOL) or
realized vol (everything else — most alts and stocks have no options market).

Pure math lives in `compute_size()` (sync, testable). `size_trade()` is the
async entrypoint that fetches price/vol and never raises.
"""

import logging
import math
import os

log = logging.getLogger(__name__)

RISK_USD  = float(os.environ.get("PAIS_TRADE_RISK_USD", "60"))
STOP_K    = 1.5                       # stop buffer in units of expected-move sigma
HOLD_DAYS = {"crypto": 3, "stock": 5}  # typical Dr. Profit hold window
YEAR_DAYS = {"crypto": 365, "stock": 252}


def compute_size(
    entry: float,
    stop_loss: float | None,
    direction: str,
    asset_class: str = "crypto",
    leverage: int = 1,
    annual_vol: float | None = None,
    take_profits: list[float] | None = None,
    risk_usd: float = RISK_USD,
) -> dict:
    """
    Deterministic sizing. If stop_loss is None, derives a vol stop
    (requires annual_vol; falls back to a 2% assumed stop without it).
    """
    direction = direction.upper()
    asset_class = asset_class if asset_class in HOLD_DAYS else "crypto"
    sign = -1 if direction == "LONG" else 1  # stop side relative to entry

    hold = HOLD_DAYS[asset_class]
    expected_move_pct = None
    if annual_vol:
        expected_move_pct = annual_vol * math.sqrt(hold / YEAR_DAYS[asset_class])

    stop_source = "signal"
    if stop_loss is None:
        if expected_move_pct:
            stop_loss = entry * (1 + sign * STOP_K * expected_move_pct)
            stop_source = f"vol ({STOP_K}σ over {hold}d hold)"
        else:
            stop_loss = entry * (1 + sign * 0.02)
            stop_source = "assumed 2% (no vol data)"
        stop_loss = round(stop_loss, 6 if entry < 10 else 2)

    stop_dist = abs(entry - stop_loss)
    if stop_dist <= 0:
        raise ValueError("Stop distance is zero — cannot size.")

    stop_pct = stop_dist / entry * 100
    units    = risk_usd / stop_dist
    notional = units * entry
    margin   = notional / max(leverage, 1)

    # Risk:reward to first take-profit
    rr = None
    tps = [tp for tp in (take_profits or []) if tp]
    if tps:
        reward = abs(tps[0] - entry)
        rr = round(reward / stop_dist, 2)

    warnings = []
    if expected_move_pct and stop_source == "signal":
        one_sigma_pct = expected_move_pct * 100
        if stop_pct < one_sigma_pct * 0.6:
            warnings.append(
                f"Stop is {stop_pct:.1f}% away but a normal {hold}d move is "
                f"±{one_sigma_pct:.1f}% — high odds of a noise stop-out.")
    if rr is not None and rr < 1.5:
        warnings.append(f"R:R to TP1 is only 1:{rr} — thin edge.")
    if margin > 1000:
        warnings.append(f"Margin required ${margin:,.0f} — check account can cover it.")

    return {
        "risk_usd":          round(risk_usd, 2),
        "entry":             entry,
        "stop_loss":         stop_loss,
        "stop_source":       stop_source,
        "stop_pct":          round(stop_pct, 2),
        "units":             round(units, 8),
        "notional":          round(notional, 2),
        "leverage":          max(leverage, 1),
        "margin":            round(margin, 2),
        "rr_to_tp1":         rr,
        "annual_vol":        round(annual_vol, 4) if annual_vol else None,
        "expected_move_pct": round(expected_move_pct * 100, 2) if expected_move_pct else None,
        "hold_days":         hold,
        "warnings":          warnings,
    }


async def size_trade(
    asset: str,
    asset_class: str,
    direction: str,
    entry: float | None = None,
    stop_loss: float | None = None,
    take_profits: list[float] | None = None,
    leverage: int = 1,
) -> dict:
    """
    Fetch live price + vol, then size at fixed $RISK_USD.
    entry=None means "enter at market" — the live price becomes the entry.
    Returns {"ok": bool, "error": str?, ...compute_size fields, "mark_price", "vol_source"}.
    """
    from tools.market_prices import get_price, get_volatility

    mark = await get_price(asset, asset_class)
    if entry is None:
        if mark is None:
            return {"ok": False,
                    "error": f"No entry in signal and no live price found for {asset}."}
        entry = mark

    vol = await get_volatility(asset, asset_class)

    try:
        sized = compute_size(
            entry        = entry,
            stop_loss    = stop_loss,
            direction    = direction,
            asset_class  = asset_class,
            leverage     = leverage,
            annual_vol   = vol["annual_vol"] if vol else None,
            take_profits = take_profits,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    sized["ok"]         = True
    sized["mark_price"] = mark
    sized["vol_source"] = vol["source"] if vol else None
    return sized
