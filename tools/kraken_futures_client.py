"""
Kraken Futures (USD-margined linear perps, "PF_" contracts) broker adapter.

Built on ccxt's `krakenfutures` exchange (async) so signing, symbol ids and
precision come from a battle-tested library instead of hand-rolled HMAC.
Public docs: https://docs.kraken.com/api/docs/futures-api/

Modes (EXECUTION_MODE in tools/exec_config.py):
    testnet -> Kraken's DEMO environment (demo-futures.kraken.com, fake funds)
    live    -> futures.kraken.com (real money)

Keys (Kraken Futures keys are SEPARATE from Kraken spot keys):
    KRAKEN_FUTURES_DEMO_API_KEY / KRAKEN_FUTURES_DEMO_API_SECRET
    KRAKEN_FUTURES_API_KEY      / KRAKEN_FUTURES_API_SECRET

Validate with `python3.11 kraken_selftest.py` before arming anything.
This module NEVER logs secrets and NEVER raises to the caller.
"""

import logging
import os

from tools.broker import Broker, OrderRequest, OrderResult

log = logging.getLogger(__name__)

MAX_TAKE_PROFITS = 2   # TP1 (partial) + TP2 (remainder); more targets can't all be reduce-only


def symbol_for(asset: str) -> str:
    """Bare ticker -> ccxt unified symbol for the USD-margined linear perp (BTC -> PF_XBTUSD)."""
    return f"{asset.upper().strip()}/USD:USD"


def _default_exchange_factory(key: str, secret: str, demo: bool):
    def make():
        import ccxt.async_support as ccxt_async
        ex = ccxt_async.krakenfutures({"apiKey": key, "secret": secret,
                                       "enableRateLimit": True})
        if demo:
            ex.set_sandbox_mode(True)
        return ex
    return make


class KrakenFuturesBroker(Broker):
    def __init__(self, demo: bool, exchange_factory=None):
        self.mode = "testnet" if demo else "live"
        prefix = "KRAKEN_FUTURES_DEMO_API" if demo else "KRAKEN_FUTURES_API"
        self.key = os.environ.get(f"{prefix}_KEY", "") or ""
        self.secret = os.environ.get(f"{prefix}_SECRET", "") or ""
        self._factory = exchange_factory or _default_exchange_factory(
            self.key, self.secret, demo)

    def configured(self) -> bool:
        return bool(self.key and self.secret)

    def _fail(self, error: str, warnings=None) -> OrderResult:
        return OrderResult(ok=False, mode=self.mode, error=error, warnings=list(warnings or []))

    # ── read ops ─────────────────────────────────────────────────────────────
    async def ping(self) -> bool:
        ex = self._factory()
        try:
            await ex.load_markets()
            return True
        except Exception as e:
            log.warning("[kraken] ping failed: %s", type(e).__name__)
            return False
        finally:
            await ex.close()

    async def get_balance(self) -> float | None:
        """Total USD-denominated margin balance, or None."""
        if not self.configured():
            return None
        ex = self._factory()
        try:
            bal = await ex.fetch_balance()
            usd = (bal.get("total") or {}).get("USD")
            return float(usd) if usd is not None else None
        except Exception as e:
            log.warning("[kraken] balance failed: %s: %s", type(e).__name__, e)
            return None
        finally:
            await ex.close()

    async def get_open_positions(self) -> list:
        if not self.configured():
            return []
        ex = self._factory()
        try:
            rows = await ex.fetch_positions()
        except Exception as e:
            log.warning("[kraken] positions failed: %s: %s", type(e).__name__, e)
            return []
        finally:
            await ex.close()
        return [{"symbol": p.get("symbol"), "contracts": p.get("contracts"),
                 "side": p.get("side"), "entryPrice": p.get("entryPrice")}
                for p in rows if float(p.get("contracts") or 0) != 0]

    # ── write op: open a protected position ─────────────────────────────────
    @staticmethod
    def _qty(ex, symbol: str, units: float) -> float:
        """Round DOWN to the contract step; 0.0 if below the minimum size."""
        try:
            return float(ex.amount_to_precision(symbol, units))
        except Exception:
            return 0.0

    def _tp_legs(self, ex, symbol: str, qty: float, req: OrderRequest) -> list:
        """[(price, size)] reduce-only take-profits whose sizes never exceed the position."""
        targets = list(req.take_profits or [])[:MAX_TAKE_PROFITS]
        if not targets:
            return []
        if not req.tp1_fraction:
            return [(targets[0], qty)]
        tp1 = self._qty(ex, symbol, qty * req.tp1_fraction)
        legs = [(targets[0], tp1)] if tp1 > 0 else []
        if len(targets) > 1:
            rest = self._qty(ex, symbol, qty - tp1)
            if rest > 0:
                legs.append((targets[1], rest))
        return legs

    async def open_position(self, req: OrderRequest) -> OrderResult:
        """
        set leverage -> entry -> stop-loss -> take-profits. If the entry fills but
        the stop does NOT, return ok=True + protected=False with a loud warning so
        the executor alerts immediately.
        """
        if not self.configured():
            env = "DEMO" if self.mode == "testnet" else "LIVE"
            return self._fail(f"Kraken Futures {env} API key/secret not set")
        ex = self._factory()
        try:
            return await self._open(ex, req)
        except Exception as e:
            return self._fail(f"unexpected {type(e).__name__}: {e}")
        finally:
            await ex.close()

    async def _open(self, ex, req: OrderRequest) -> OrderResult:
        symbol = symbol_for(req.asset)
        warnings: list = []
        try:
            markets = await ex.load_markets()
        except Exception as e:
            return self._fail(f"could not load Kraken markets: {type(e).__name__}: {e}")
        if symbol not in markets:
            return self._fail(f"{req.asset} is not listed on Kraken Futures ({symbol})")

        qty = self._qty(ex, symbol, req.units)
        if qty <= 0:
            return self._fail(f"computed quantity {req.units:g} is below the "
                              f"{symbol} minimum size — nothing to place")

        try:
            await ex.set_leverage(int(req.leverage), symbol)
        except Exception as e:
            warnings.append(f"set_leverage failed ({type(e).__name__}: {e}); "
                            f"using account default")

        side, close_side = req.side.lower(), req.close_side.lower()
        order_type = "limit" if req.order_type == "LIMIT" else "market"
        price = req.entry if order_type == "limit" else None
        entry_params = {"clientOrderId": req.client_id[:100]} if req.client_id else {}
        try:
            entry = await ex.create_order(symbol, order_type, side, qty, price, entry_params)
        except Exception as e:
            return self._fail(f"entry order rejected: {type(e).__name__}: {e}", warnings)

        order_id = str(entry.get("id") or "") or None
        fill = entry.get("average") or entry.get("price") or req.entry

        stop_order_id, protected = None, False
        if req.stop_loss:
            try:
                stop = await ex.create_order(symbol, "market", close_side, qty, None,
                                             {"stopLossPrice": req.stop_loss,
                                              "reduceOnly": True})
                stop_order_id, protected = str(stop.get("id") or ""), True
            except Exception as e:
                warnings.append(f"⚠️ STOP-LOSS FAILED ({type(e).__name__}: {e}) — position "
                                f"is UNPROTECTED, place a manual stop now")
        else:
            warnings.append("no stop-loss in signal — position opened without a hard stop")

        tp_ids: list = []
        for tp_price, tp_qty in self._tp_legs(ex, symbol, qty, req):
            try:
                tp = await ex.create_order(symbol, "market", close_side, tp_qty, None,
                                           {"takeProfitPrice": tp_price, "reduceOnly": True})
                tp_ids.append(str(tp.get("id") or ""))
            except Exception as e:
                warnings.append(f"take-profit @ {tp_price} not placed ({type(e).__name__}: {e})")

        return OrderResult(
            ok=True, mode=self.mode, order_id=order_id,
            avg_fill_price=float(fill), filled_units=qty,
            stop_order_id=stop_order_id, tp_order_ids=tp_ids,
            protected=protected, warnings=warnings, raw=entry.get("info"),
        )
