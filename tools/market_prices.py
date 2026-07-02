"""
Market prices + volatility for live trading.

Crypto:  Kraken public API → Binance fallback (no keys needed).
Stocks:  Yahoo Finance chart API (no key) → Stooq CSV fallback.
Vol:     Deribit DVOL (implied, BTC/ETH only) → realized vol from
         daily closes for everything else.

All functions are async, never raise — they return None on failure so
callers degrade gracefully. Results are TTL-cached so dashboard refresh
polling doesn't hammer the public APIs.
"""

import asyncio
import logging
import math
import time

import httpx

log = logging.getLogger(__name__)

PRICE_TTL_SECS = 20        # dashboard polls every ~30s; keep sub-poll fresh
VOL_TTL_SECS   = 3600      # vol moves slowly; hourly is plenty
_HTTP_TIMEOUT  = 8.0
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) PAIS/1.0"}

# Kraken ticker quirks
_KRAKEN_ALIASES = {"BTC": "XBT", "DOGE": "XDG"}

# Index/CFD tickers as quoted by trading platforms → Yahoo symbols
_YAHOO_ALIASES = {"SP500": "^GSPC", "SPX": "^GSPC", "US500": "^GSPC",
                  "NAS100": "^NDX", "NASDAQ": "^IXIC", "US30": "^DJI"}

# symbol → (value, expires_at)
_price_cache: dict = {}
_vol_cache: dict = {}


def _cache_get(cache: dict, key: str):
    hit = cache.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    return None


def _cache_put(cache: dict, key: str, value, ttl: float):
    cache[key] = (value, time.time() + ttl)


# ── Prices ────────────────────────────────────────────────────────────────────

async def _kraken_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    pair = f"{_KRAKEN_ALIASES.get(symbol, symbol)}USD"
    r = await client.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair})
    data = r.json()
    if data.get("error"):
        return None
    result = data.get("result") or {}
    if not result:
        return None
    first = next(iter(result.values()))
    return float(first["c"][0])


async def _binance_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    r = await client.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": f"{symbol}USDT"})
    data = r.json()
    if "price" not in data:
        return None
    return float(data["price"])


async def _yahoo_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    symbol = _YAHOO_ALIASES.get(symbol, symbol)
    r = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": "1d", "range": "1d"}, headers=_UA)
    meta = (((r.json().get("chart") or {}).get("result") or [{}])[0].get("meta") or {})
    price = meta.get("regularMarketPrice")
    return float(price) if price else None


async def _stooq_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    r = await client.get(f"https://stooq.com/q/l/?s={symbol.lower()}.us&f=sd2t2ohlcv&h&e=csv",
                         headers=_UA)
    lines = r.text.strip().splitlines()
    if len(lines) < 2:
        return None
    close = lines[1].split(",")[6]
    return float(close) if close not in ("", "N/D") else None


async def get_price(symbol: str, asset_class: str = "crypto") -> float | None:
    """Current price in USD, or None. Cached ~20s."""
    symbol = symbol.upper().strip()
    key = f"{asset_class}:{symbol}"
    cached = _cache_get(_price_cache, key)
    if cached is not None:
        return cached

    sources = ([_kraken_price, _binance_price] if asset_class == "crypto"
               else [_yahoo_price, _stooq_price])
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for fn in sources:
            try:
                price = await fn(client, symbol)
                if price and price > 0:
                    _cache_put(_price_cache, key, price, PRICE_TTL_SECS)
                    return price
            except Exception as e:
                log.debug("[prices] %s failed for %s: %s", fn.__name__, symbol, e)
    log.warning("[prices] No price found for %s (%s)", symbol, asset_class)
    return None


async def get_prices(assets: list[tuple[str, str]]) -> dict[str, float]:
    """Batch fetch. assets = [(symbol, asset_class), ...] → {symbol: price}."""
    unique = list({(s.upper(), c) for s, c in assets})
    results = await asyncio.gather(*(get_price(s, c) for s, c in unique))
    return {s: p for (s, _), p in zip(unique, results) if p is not None}


# ── Volatility ────────────────────────────────────────────────────────────────

async def _deribit_dvol(client: httpx.AsyncClient, symbol: str) -> float | None:
    """Implied vol index (DVOL) for BTC/ETH, as annualized decimal (0.45 = 45%)."""
    r = await client.get("https://www.deribit.com/api/v2/public/get_index_price",
                         params={"index_name": f"{symbol.lower()}dvol_usdc"})
    price = (r.json().get("result") or {}).get("index_price")
    return float(price) / 100 if price else None


async def _kraken_daily_closes(client: httpx.AsyncClient, symbol: str) -> list[float]:
    pair = f"{_KRAKEN_ALIASES.get(symbol, symbol)}USD"
    r = await client.get("https://api.kraken.com/0/public/OHLC",
                         params={"pair": pair, "interval": 1440})
    data = r.json()
    if data.get("error"):
        return []
    result = {k: v for k, v in (data.get("result") or {}).items() if k != "last"}
    if not result:
        return []
    candles = next(iter(result.values()))
    return [float(c[4]) for c in candles[-46:]]  # ~45 daily closes


async def _binance_daily_closes(client: httpx.AsyncClient, symbol: str) -> list[float]:
    r = await client.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": f"{symbol}USDT", "interval": "1d", "limit": 46})
    data = r.json()
    if not isinstance(data, list):
        return []
    return [float(c[4]) for c in data]


async def _yahoo_daily_closes(client: httpx.AsyncClient, symbol: str) -> list[float]:
    symbol = _YAHOO_ALIASES.get(symbol, symbol)
    r = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": "1d", "range": "3mo"}, headers=_UA)
    result = ((r.json().get("chart") or {}).get("result") or [{}])[0]
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    return [float(c) for c in closes if c is not None]


def _realized_vol(closes: list[float], periods_per_year: int) -> float | None:
    """Annualized stdev of daily log returns."""
    if len(closes) < 15:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 14:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


async def get_volatility(symbol: str, asset_class: str = "crypto") -> dict | None:
    """
    Annualized volatility for an asset.
    Returns {"annual_vol": 0.45, "source": "implied (Deribit DVOL)" | "realized (45d)"}
    or None if no data source worked.
    """
    symbol = symbol.upper().strip()
    key = f"{asset_class}:{symbol}"
    cached = _cache_get(_vol_cache, key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # Implied vol first — only exists for BTC/ETH via Deribit DVOL
        if asset_class == "crypto" and symbol in ("BTC", "ETH"):
            try:
                iv = await _deribit_dvol(client, symbol)
                if iv:
                    out = {"annual_vol": round(iv, 4), "source": "implied (Deribit DVOL)"}
                    _cache_put(_vol_cache, key, out, VOL_TTL_SECS)
                    return out
            except Exception as e:
                log.debug("[vol] DVOL failed for %s: %s", symbol, e)

        # Realized vol fallback
        if asset_class == "crypto":
            fetchers, ppy = [_kraken_daily_closes, _binance_daily_closes], 365
        else:
            fetchers, ppy = [_yahoo_daily_closes], 252
        for fn in fetchers:
            try:
                closes = await fn(client, symbol)
                rv = _realized_vol(closes, ppy)
                if rv:
                    out = {"annual_vol": round(rv, 4),
                           "source": f"realized ({len(closes)}d closes)"}
                    _cache_put(_vol_cache, key, out, VOL_TTL_SECS)
                    return out
            except Exception as e:
                log.debug("[vol] %s failed for %s: %s", fn.__name__, symbol, e)

    log.warning("[vol] No volatility data for %s (%s)", symbol, asset_class)
    return None
