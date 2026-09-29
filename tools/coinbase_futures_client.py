"""
Coinbase US futures broker — CFTC-regulated "perp-style" contracts (e.g.
BIP-20DEC30-CDE = 0.01 BTC) traded through the Advanced Trade API with the
official SDK (coinbase-advanced-py). US futures only live in the DEFAULT
portfolio, so the CDP API key must be scoped to it.

Fee strategy (lowest cost first):
  * Entries go in as POST-ONLY limit orders -> always maker (promo 0% maker vs
    ~0.03% taker; per-contract minimums apply). EXEC_ENTRY_STYLE picks what
    happens when a "buy now" signal's maker order doesn't fill:
        maker_first (default) — rest at the touch for EXEC_MAKER_WAIT_SECS, then
                                fall back to a market order for the unfilled size
        maker_only            — never pay taker; skip the trade if unfilled
        taker                 — market order straight away
  * A LIMIT signal ("buy at $X") rests post-only at X until filled.
  * Stop + take-profit ride on the entry as ONE attached bracket
    (trigger_bracket_gtc): the TP is a resting limit (maker) and the stop
    becomes a stop-limit with a 5% collar when triggered. Coinbase allows one
    bracket per position, so the full size exits at the FIRST target.

No testnet exists. Validate with `python3.11 coinbase_selftest.py`, which
checks auth/balance/fee tier and PREVIEWS an order without executing it.

Keys: COINBASE_KEY_FILE (path to the downloaded CDP JSON key, preferred) or
COINBASE_API_KEY + COINBASE_API_SECRET. Never logged; this module never raises.
"""

import asyncio
import logging
import math
import os
from pathlib import Path

from tools.broker import Broker, OrderRequest, OrderResult

log = logging.getLogger(__name__)

STYLE_MAKER_FIRST = "maker_first"
STYLE_MAKER_ONLY = "maker_only"
STYLE_TAKER = "taker"
_STYLES = {STYLE_MAKER_FIRST, STYLE_MAKER_ONLY, STYLE_TAKER}

NO_TP_R_MULTIPLE = 5          # bracket needs a TP; without one, park it at 5R
POST_ONLY_CROSSED = "INVALID_LIMIT_PRICE_POST_ONLY"
_DONE = {"FILLED", "CANCELLED", "EXPIRED", "FAILED"}


# ── pure helpers ─────────────────────────────────────────────────────────────

def _d(resp) -> dict:
    """SDK responses are objects with to_dict(); fakes/raw calls are dicts."""
    if resp is None:
        return {}
    return resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)


def perp_products(resp) -> dict:
    """{ROOT: {product_id, contract_size, tick}} for crypto perp-style contracts only
    (skips dated futures, index/commodity perps, and 1000x-quoted memecoin perps)."""
    out = {}
    for p in _d(resp).get("products", []):
        f = p.get("future_product_details") or {}
        name = str(f.get("display_name") or "")
        root = str(f.get("contract_root_unit") or "").upper()
        if not name.endswith("Perpetual") or name.startswith("1000"):
            continue
        if not root or root.startswith("CDE") or root == "PAXG":
            continue
        try:
            out[root] = {"product_id": p["product_id"],
                         "contract_size": float(f["contract_size"]),
                         "tick": float(p.get("price_increment") or p.get("quote_increment") or 0)}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def contracts_for(units: float, contract_size: float) -> int:
    """Whole contracts, rounded DOWN so the $-risk can only shrink."""
    if contract_size <= 0:
        return 0
    return int(math.floor(units / contract_size + 1e-9))


def round_to_tick(price: float, tick: float) -> str:
    if not tick or tick <= 0:
        return f"{price:g}"
    steps = round(price / tick)
    decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return f"{steps * tick:.{decimals}f}"


# ── broker ───────────────────────────────────────────────────────────────────

class CoinbaseFuturesBroker(Broker):
    mode = "live"

    def __init__(self, client_factory=None, entry_style: str | None = None,
                 maker_wait_secs: float | None = None, poll_secs: float = 2.0):
        self.key_file = os.environ.get("COINBASE_KEY_FILE", "") or ""
        self.key = os.environ.get("COINBASE_API_KEY", "") or ""
        self.secret = (os.environ.get("COINBASE_API_SECRET", "") or "").replace("\\n", "\n")
        style = (entry_style or os.environ.get("EXEC_ENTRY_STYLE", STYLE_MAKER_FIRST)).lower()
        self.entry_style = style if style in _STYLES else STYLE_MAKER_FIRST
        self.maker_wait_secs = (maker_wait_secs if maker_wait_secs is not None
                                else float(os.environ.get("EXEC_MAKER_WAIT_SECS", "30") or 30))
        self.poll_secs = poll_secs
        self._factory = client_factory or self._default_client
        self._client = None

    def configured(self) -> bool:
        if self.key_file and Path(self.key_file).is_file():
            return True
        return bool(self.key and self.secret)

    def _default_client(self):
        from coinbase.rest import RESTClient
        if self.key_file and Path(self.key_file).is_file():
            return RESTClient(key_file=self.key_file, timeout=10)
        if self.key and self.secret:
            return RESTClient(api_key=self.key, api_secret=self.secret, timeout=10)
        return RESTClient(timeout=10)       # unauthenticated: public market data only

    async def _call(self, method: str, *args, **kwargs) -> dict:
        if self._client is None:
            self._client = self._factory()
        fn = getattr(self._client, method)
        return _d(await asyncio.to_thread(fn, *args, **kwargs))

    def _fail(self, error: str, warnings=None) -> OrderResult:
        return OrderResult(ok=False, mode=self.mode, error=error, warnings=list(warnings or []))

    # ── reads ────────────────────────────────────────────────────────────────
    async def ping(self) -> bool:
        try:
            return bool(perp_products(await self._call(
                "get_public_products", product_type="FUTURE", get_all_products=True)))
        except Exception as e:
            log.warning("[coinbase] ping failed: %s", type(e).__name__)
            return False

    async def get_balance(self) -> float | None:
        if not self.configured():
            return None
        try:
            s = (await self._call("get_futures_balance_summary")).get("balance_summary") or {}
            return float((s.get("total_usd_balance") or {}).get("value"))
        except Exception as e:
            log.warning("[coinbase] balance failed: %s: %s", type(e).__name__, e)
            return None

    async def get_open_positions(self) -> list:
        if not self.configured():
            return []
        try:
            rows = (await self._call("list_futures_positions")).get("positions") or []
        except Exception as e:
            log.warning("[coinbase] positions failed: %s: %s", type(e).__name__, e)
            return []
        out = []
        for p in rows:
            n = float(p.get("number_of_contracts") or 0)
            if n:
                out.append({"symbol": p.get("product_id"), "side": p.get("side"),
                            "contracts": n, "entryPrice": float(p.get("avg_entry_price") or 0)})
        return out

    async def get_fee_tier(self) -> dict | None:
        """The account's ACTUAL futures maker/taker rates (volume/promo tier)."""
        try:
            t = (await self._call("get_transaction_summary", product_type="FUTURE")
                 ).get("fee_tier") or {}
            return {"tier": t.get("pricing_tier"), "maker": float(t.get("maker_fee_rate")),
                    "taker": float(t.get("taker_fee_rate"))}
        except Exception as e:
            log.warning("[coinbase] fee tier failed: %s: %s", type(e).__name__, e)
            return None

    # ── order plumbing ───────────────────────────────────────────────────────
    async def _submit(self, cid: str, pid: str, side: str, config: dict,
                      bracket: dict | None) -> tuple[str | None, str | None]:
        """Returns (order_id, error_code)."""
        extra = {"attached_order_configuration": bracket} if bracket else {}
        try:
            r = await self._call("create_order", client_order_id=cid, product_id=pid,
                                 side=side, order_configuration=config, **extra)
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
        if not r.get("success"):
            err = r.get("error_response") or {}
            return None, (err.get("error") or err.get("preview_failure_reason")
                          or err.get("message") or "order rejected")
        return (r.get("success_response") or {}).get("order_id"), None

    async def _order(self, order_id: str) -> dict:
        try:
            return (await self._call("get_order", order_id)).get("order") or {}
        except Exception:
            return {}

    async def _wait_fill(self, order_id: str, secs: float) -> dict:
        """Poll until the order is done or `secs` elapse; returns the last order snapshot."""
        waited = 0.0
        o = await self._order(order_id)
        while o.get("status") not in _DONE and waited < secs:
            await asyncio.sleep(self.poll_secs)
            waited += self.poll_secs or 1.0
            o = await self._order(order_id)
        return o

    async def _touch(self, pid: str, side: str) -> float | None:
        """Best bid for a BUY / best ask for a SELL — the price that rests as maker."""
        try:
            books = (await self._call("get_best_bid_ask", product_ids=[pid])).get("pricebooks")
            levels = books[0]["bids" if side == "BUY" else "asks"]
            return float(levels[0]["price"])
        except Exception:
            return None

    def _bracket(self, req: OrderRequest, tick: float, warnings: list) -> dict | None:
        if not req.stop_loss:
            warnings.append("no stop-loss in signal — position opened without a hard stop "
                            "(UNPROTECTED)")
            return None
        if req.take_profits:
            tp = req.take_profits[0]
            if len(req.take_profits) > 1:
                warnings.append(f"Coinbase allows one bracket per position — full size "
                                f"exits at TP1 {tp:g}")
        else:
            tp = req.entry + NO_TP_R_MULTIPLE * (req.entry - req.stop_loss)
            warnings.append(f"no take-profit in signal — bracket TP parked at "
                            f"{NO_TP_R_MULTIPLE}R ({tp:g})")
        return {"trigger_bracket_gtc": {"limit_price": round_to_tick(tp, tick),
                                        "stop_trigger_price": round_to_tick(req.stop_loss, tick)}}

    # ── open ─────────────────────────────────────────────────────────────────
    async def open_position(self, req: OrderRequest) -> OrderResult:
        if not self.configured():
            return self._fail("Coinbase API key not set (COINBASE_KEY_FILE or "
                              "COINBASE_API_KEY/SECRET)")
        try:
            return await self._open(req)
        except Exception as e:
            return self._fail(f"unexpected {type(e).__name__}: {e}")

    async def _open(self, req: OrderRequest) -> OrderResult:
        warnings: list = []
        try:
            products = perp_products(await self._call(
                "get_public_products", product_type="FUTURE", get_all_products=True))
        except Exception as e:
            return self._fail(f"could not load Coinbase futures products: {type(e).__name__}")
        prod = products.get(req.asset.upper().strip())
        if not prod:
            return self._fail(f"{req.asset}: no Coinbase US perp-style contract")

        pid, csize, tick = prod["product_id"], prod["contract_size"], prod["tick"]
        n = contracts_for(req.units, csize)
        if n < 1:
            return self._fail(f"size {req.units:g} {req.asset} is below 1 contract "
                              f"({csize:g} {req.asset} ≈ ${csize * req.entry:,.0f}) — the "
                              f"$-risk is too small for this contract")
        if n * csize < req.units * 0.999:
            warnings.append(f"rounded down to {n} contract(s) = {n * csize:g} {req.asset}")

        side = req.side
        bracket = self._bracket(req, tick, warnings)
        cid = (req.client_id or "drp")[:40]

        if req.order_type == "LIMIT":
            price = round_to_tick(req.entry, tick)
            oid, err = await self._submit(
                f"{cid}-L", pid, side,
                {"limit_limit_gtc": {"base_size": str(n), "limit_price": price,
                                     "post_only": True}}, bracket)
            if oid:
                warnings.append(f"post-only limit resting at {price} (maker fee)")
                return self._result(oid, None, n, csize, req.entry, bracket, warnings)
            if err != POST_ONLY_CROSSED:
                return self._fail(f"entry order rejected: {err}", warnings)
            warnings.append(f"limit {price} already crossed — entering at market instead")

        return await self._enter_now(req, pid, side, n, csize, tick, bracket, cid, warnings)

    async def _enter_now(self, req, pid, side, n, csize, tick, bracket, cid,
                         warnings) -> OrderResult:
        if self.entry_style != STYLE_TAKER:
            touch = await self._touch(pid, side)
            if touch:
                price = round_to_tick(touch, tick)
                oid, err = await self._submit(
                    f"{cid}-M", pid, side,
                    {"limit_limit_gtc": {"base_size": str(n), "limit_price": price,
                                         "post_only": True}}, bracket)
                if oid:
                    o = await self._wait_fill(oid, self.maker_wait_secs)
                    if o.get("status") == "FILLED":
                        warnings.append(f"entry filled as maker (post-only) at {price}")
                        return self._result(oid, o, n, csize, req.entry, bracket, warnings)
                    await self._call("cancel_orders", order_ids=[oid])
                    o = await self._order(oid) or o
                    filled = int(float(o.get("filled_size") or 0))
                    if filled > 0:
                        warnings.append(f"partial maker fill {filled}/{n} contracts — "
                                        f"remainder cancelled, not chased (verify the "
                                        f"bracket covers {filled})")
                        return self._result(oid, o, filled, csize, req.entry, bracket, warnings)
                else:
                    warnings.append(f"maker entry rejected ({err})")
            else:
                warnings.append("no order book quote for a maker entry")
            if self.entry_style == STYLE_MAKER_ONLY:
                return self._fail("maker entry not filled — skipped (EXEC_ENTRY_STYLE="
                                  "maker_only never pays taker)", warnings)
            warnings.append("maker entry not filled — fell back to market (taker fee)")

        oid, err = await self._submit(f"{cid}-T", pid, side,
                                      {"market_market_ioc": {"base_size": str(n)}}, bracket)
        if not oid:
            return self._fail(f"entry order rejected: {err}", warnings)
        o = await self._wait_fill(oid, 5.0)
        return self._result(oid, o, n, csize, req.entry, bracket, warnings)

    def _result(self, oid, order, contracts, csize, ref_price, bracket, warnings) -> OrderResult:
        order = order or {}
        try:
            fill = float(order.get("average_filled_price") or 0) or ref_price
        except (TypeError, ValueError):
            fill = ref_price
        return OrderResult(
            ok=True, mode=self.mode, order_id=oid, avg_fill_price=fill,
            filled_units=contracts * csize,
            stop_order_id=f"{oid}:bracket" if bracket else None,
            tp_order_ids=[f"{oid}:bracket"] if bracket else [],
            protected=bracket is not None, warnings=warnings,
            raw={"order": order, "contracts": contracts},
        )
