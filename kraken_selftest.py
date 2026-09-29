"""
Kraken Futures adapter self-test — run the moment keys are in .env, BEFORE
trusting the auto-executor with anything.

    python3.11 kraken_selftest.py                # markets + auth + balance + positions (read-only)
    python3.11 kraken_selftest.py --test-order   # tiny open (with stop) + flatten — DEMO only

Targets the demo environment unless EXECUTION_MODE=live. --test-order refuses
to run against live.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


async def _flatten(broker, symbol: str, side: str, qty: float) -> str | None:
    ex = broker._factory()
    try:
        await ex.cancel_all_orders(symbol)
        await ex.create_order(symbol, "market", side, qty, None, {"reduceOnly": True})
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    finally:
        await ex.close()


async def main() -> int:
    demo = os.environ.get("EXECUTION_MODE", "testnet").strip().lower() != "live"
    env = "DEMO" if demo else "LIVE"
    from tools.kraken_futures_client import KrakenFuturesBroker, symbol_for
    broker = KrakenFuturesBroker(demo=demo)
    print(f"\n=== Kraken Futures self-test ({env}) ===\n")

    if not broker.configured():
        var = "KRAKEN_FUTURES_DEMO_API_KEY/SECRET" if demo else "KRAKEN_FUTURES_API_KEY/SECRET"
        print(f"✗ No API key/secret for {env}. Set {var} in .env.")
        return 1

    print("1. markets (public)...", end=" ", flush=True)
    if not await broker.ping():
        print("✗ FAILED — network or Kraken API unreachable")
        return 1
    print("✓")

    print("2. auth + balance...", end=" ", flush=True)
    bal = await broker.get_balance()
    if bal is None:
        print("✗ FAILED — key/secret wrong, key lacks permission, or wrong environment "
              "(demo keys only work on demo, live keys only on live)")
        return 1
    print(f"✓  USD margin balance: {bal:,.2f}")

    print("3. open positions...", end=" ", flush=True)
    pos = await broker.get_open_positions()
    print(f"✓  {len(pos)} open")
    for p in pos:
        print(f"     {p}")

    if "--test-order" not in sys.argv:
        print("\nRead-only checks passed. Re-run with --test-order for a demo round-trip.\n")
        return 0
    if not demo:
        print("\n✗ Refusing --test-order in LIVE mode. Set EXECUTION_MODE=testnet.\n")
        return 1

    from tools.broker import OrderRequest
    from tools.market_prices import get_price
    asset = os.environ.get("KRAKEN_SELFTEST_ASSET", "BTC")
    px = await get_price(asset, "crypto") or 0
    if px <= 0:
        print(f"\n✗ Could not fetch a reference price for {asset}.\n")
        return 1
    notional = float(os.environ.get("KRAKEN_SELFTEST_NOTIONAL", "20"))
    units = notional / px

    print(f"\n4. TEST ORDER: LONG ~${notional:.0f} {asset} MARKET, stop 5% below...")
    req = OrderRequest(asset=asset, direction="LONG", units=units, leverage=2, entry=px,
                       order_type="MARKET", stop_loss=round(px * 0.95, 2),
                       take_profits=[], client_id="selftest")
    res = await broker.open_position(req)
    print(f"   -> ok={res.ok} id={res.order_id} fill={res.avg_fill_price} "
          f"size={res.filled_units} protected={res.protected} err={res.error}")
    for w in res.warnings:
        print(f"      • {w}")
    if not res.ok:
        return 1

    print("5. flatten (cancel stop + reduce-only market close)...", end=" ", flush=True)
    err = await _flatten(broker, symbol_for(asset), "sell", res.filled_units)
    print("✓ closed" if err is None else f"✗ FAILED — flatten manually in the demo UI! ({err})")
    print("\nDone.\n")
    return 0 if (err is None and res.protected) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
