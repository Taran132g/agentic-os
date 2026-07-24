"""
Yubit USDT-M perpetual futures REST adapter.

⚠️  VERIFY BEFORE LIVE — Yubit's API docs (https://openapi.yubit.com/en-US/)
    are WAF-protected and could not be read programmatically while building this.
    The endpoint paths, header names, signing recipe, and symbol format below are
    modeled on the standard USDT-M futures API shape (Binance-fapi lineage, which
    most smaller USDT-M venues clone). Each ⚠️ constant below is a thing to confirm
    against the live docs. After confirming, run:

        python3.11 yubit_selftest.py            # ping + auth + balance (testnet)
        python3.11 yubit_selftest.py --test-order   # tiny open+close on testnet

    before ever setting EXECUTION_MODE=live.

    Confirm, in order:
      1. BASE_URL_LIVE / BASE_URL_TESTNET
      2. API_KEY_HEADER  and how the signature is transmitted (query vs header)
      3. Signature payload string + digest encoding (hex vs base64)
      4. EP_* endpoint paths + HTTP methods
      5. Order param names/values (side, type, quantity, reduceOnly, stopPrice)
      6. symbol_for() format and quantity step size / precision

This module NEVER logs secrets and NEVER raises to the caller.
"""

import hashlib
import hmac
import logging
import os
import time
import urllib.parse

import httpx

from tools.broker import Broker, OrderRequest, OrderResult

log = logging.getLogger(__name__)

# ── ⚠️ VERIFY: environment + endpoints ──────────────────────────────────────
BASE_URL_LIVE    = os.environ.get("YUBIT_BASE_URL",         "https://openapi.yubit.com")
BASE_URL_TESTNET = os.environ.get("YUBIT_TESTNET_BASE_URL", "https://testnet-openapi.yubit.com")
API_KEY_HEADER   = os.environ.get("YUBIT_API_KEY_HEADER",   "X-MBX-APIKEY")

RECV_WINDOW_MS   = 5000
_HTTP_TIMEOUT    = 10.0
_QTY_DECIMALS    = int(os.environ.get("YUBIT_QTY_DECIMALS", "6") or "6")

# (HTTP method, path). ⚠️ VERIFY every path against the live docs.
EP_SERVER_TIME = ("GET",  "/fapi/v1/time")
EP_BALANCE     = ("GET",  "/fapi/v2/balance")
EP_LEVERAGE    = ("POST", "/fapi/v1/leverage")
EP_ORDER       = ("POST", "/fapi/v1/order")
EP_POSITIONS   = ("GET",  "/fapi/v2/positionRisk")


def symbol_for(asset: str) -> str:
    """Bare ticker -> exchange symbol. ⚠️ VERIFY format (e.g. BTCUSDT vs BTC-USDT)."""
    return f"{asset.upper().strip()}USDT"


def _round_qty(units: float) -> float:
    # ⚠️ VERIFY: real venues enforce per-symbol step sizes via exchangeInfo.
    # Flat rounding here can be rejected for precision on some symbols.
    return round(units, _QTY_DECIMALS)


class YubitBroker(Broker):
    def __init__(self, testnet: bool):
        self.mode = "testnet" if testnet else "live"
        self.base = BASE_URL_TESTNET if testnet else BASE_URL_LIVE
        if testnet:
            self.key    = os.environ.get("YUBIT_TESTNET_API_KEY", "") or ""
            self.secret = os.environ.get("YUBIT_TESTNET_API_SECRET", "") or ""
        else:
            self.key    = os.environ.get("YUBIT_API_KEY", "") or ""
            self.secret = os.environ.get("YUBIT_API_SECRET", "") or ""

    def configured(self) -> bool:
        return bool(self.key and self.secret)

    # ── signing ─────────────────────────────────────────────────────────────
    def _signed_query(self, params: dict) -> str:
        """
        ⚠️ VERIFY signing. Binance-fapi style: HMAC-SHA256 (hex) over the
        urlencoded param string that INCLUDES timestamp & recvWindow; the
        signature is appended as &signature=... and the API key rides in a
        header. If Yubit uses Bybit-style header signing instead, this is the
        one function to rewrite.
        """
        p = dict(params)
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = RECV_WINDOW_MS
        query = urllib.parse.urlencode(sorted(p.items()))
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    async def _request(self, ep: tuple, params: dict | None = None,
                       signed: bool = True) -> tuple[bool, dict | None, str | None]:
        """Returns (ok, json, error). Never raises. Never logs the secret."""
        if signed and not self.configured():
            return False, None, "Yubit API key/secret not set for this mode"
        method, path = ep
        params = dict(params or {})
        headers = {API_KEY_HEADER: self.key} if signed or self.key else {}
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                if signed:
                    qs = self._signed_query(params)
                    if method == "GET":
                        r = await client.get(f"{url}?{qs}", headers=headers)
                    else:
                        r = await client.post(f"{url}?{qs}", headers=headers)
                else:
                    r = await client.request(method, url, params=params, headers=headers)
            if r.status_code >= 400:
                # Surface the exchange's error body (never contains our secret).
                return False, None, f"HTTP {r.status_code}: {r.text[:300]}"
            try:
                return True, r.json(), None
            except ValueError:
                return True, {"_text": r.text[:300]}, None
        except Exception as e:
            return False, None, f"{type(e).__name__}: {e}"

    # ── read ops ─────────────────────────────────────────────────────────────
    async def ping(self) -> bool:
        ok, _, _ = await self._request(EP_SERVER_TIME, signed=False)
        return ok

    async def get_balance(self) -> float | None:
        """USDT wallet balance, or None. ⚠️ VERIFY response shape."""
        ok, data, err = await self._request(EP_BALANCE, signed=True)
        if not ok or data is None:
            log.warning("[yubit] balance failed: %s", err)
            return None
        try:
            if isinstance(data, list):
                for row in data:
                    if str(row.get("asset", "")).upper() == "USDT":
                        return float(row.get("balance") or row.get("availableBalance") or 0)
            if isinstance(data, dict):
                return float(data.get("balance") or data.get("availableBalance") or 0)
        except (TypeError, ValueError):
            pass
        return None

    async def get_open_positions(self) -> list:
        ok, data, err = await self._request(EP_POSITIONS, signed=True)
        if not ok or not isinstance(data, list):
            return []
        out = []
        for p in data:
            try:
                amt = float(p.get("positionAmt") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt != 0:
                out.append({"symbol": p.get("symbol"), "positionAmt": amt,
                            "entryPrice": p.get("entryPrice")})
        return out

    # ── write op: open a protected position ─────────────────────────────────
    async def _set_leverage(self, symbol: str, leverage: int) -> str | None:
        ok, _, err = await self._request(
            EP_LEVERAGE, {"symbol": symbol, "leverage": int(leverage)}, signed=True)
        return None if ok else (err or "set_leverage failed")

    async def open_position(self, req: OrderRequest) -> OrderResult:
        """
        Open the position and attach protection. Order of operations matters for
        real money: set leverage -> entry -> stop-loss -> take-profit. If the
        entry fills but the stop-loss does NOT, we return ok=True but
        protected=False with a loud warning so the caller can alert immediately.
        """
        symbol = symbol_for(req.asset)
        qty = _round_qty(req.units)
        warnings: list = []

        if qty <= 0:
            return OrderResult(ok=False, mode=self.mode,
                               error=f"computed quantity is {qty} — nothing to place")

        lev_err = await self._set_leverage(symbol, req.leverage)
        if lev_err:
            warnings.append(f"set_leverage failed ({lev_err}); using account default")

        # ── entry ──
        entry_params = {
            "symbol":   symbol,
            "side":     req.side,
            "type":     req.order_type,
            "quantity": qty,
        }
        if req.order_type == "LIMIT":
            entry_params["price"] = req.entry
            entry_params["timeInForce"] = "GTC"
        if req.client_id:
            entry_params["newClientOrderId"] = req.client_id[:36]

        ok, data, err = await self._request(EP_ORDER, entry_params, signed=True)
        if not ok:
            return OrderResult(ok=False, mode=self.mode,
                               error=f"entry order rejected: {err}", warnings=warnings)

        order_id = str((data or {}).get("orderId") or (data or {}).get("clientOrderId") or "")
        try:
            fill = float((data or {}).get("avgPrice") or 0) or req.entry
        except (TypeError, ValueError):
            fill = req.entry

        # ── stop-loss (reduce-only STOP_MARKET) ──
        stop_order_id = None
        protected = False
        if req.stop_loss:
            sl_params = {
                "symbol":       symbol,
                "side":         req.close_side,
                "type":         "STOP_MARKET",
                "stopPrice":    req.stop_loss,
                "closePosition": "true",
                "reduceOnly":   "true",
            }
            sl_ok, sl_data, sl_err = await self._request(EP_ORDER, sl_params, signed=True)
            if sl_ok:
                stop_order_id = str((sl_data or {}).get("orderId") or "")
                protected = True
            else:
                warnings.append(
                    f"⚠️ STOP-LOSS FAILED ({sl_err}) — position is UNPROTECTED, "
                    f"place a manual stop now")
        else:
            warnings.append("no stop-loss in signal — position opened without a hard stop")

        # ── take-profit (optional partial, reduce-only) ──
        tp_ids: list = []
        for tp in (req.take_profits or [])[:3]:
            tp_params = {
                "symbol":     symbol,
                "side":       req.close_side,
                "type":       "TAKE_PROFIT_MARKET",
                "stopPrice":  tp,
                "reduceOnly": "true",
            }
            if req.tp1_fraction and not tp_ids:
                tp_params["quantity"] = _round_qty(qty * req.tp1_fraction)
            else:
                tp_params["closePosition"] = "true"
            tp_ok, tp_data, tp_err = await self._request(EP_ORDER, tp_params, signed=True)
            if tp_ok:
                tp_ids.append(str((tp_data or {}).get("orderId") or ""))
            else:
                warnings.append(f"take-profit @ {tp} not placed ({tp_err})")

        return OrderResult(
            ok=True, mode=self.mode, order_id=order_id or None,
            avg_fill_price=fill, filled_units=qty,
            stop_order_id=stop_order_id, tp_order_ids=tp_ids,
            protected=protected, warnings=warnings, raw=data,
        )
