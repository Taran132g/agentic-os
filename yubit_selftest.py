"""
Yubit adapter self-test — run this the moment testnet keys are in .env, BEFORE
trusting the auto-executor with anything.

    python3.11 yubit_selftest.py                # ping + auth + balance (safe, read-only)
    python3.11 yubit_selftest.py --test-order   # tiny open + immediate flatten (TESTNET only)

It exercises tools/yubit_client.py against whatever EXECUTION_MODE points at
(testnet by default here). If ping or balance fail, the signing recipe /
endpoints / header names in yubit_client.py need correcting against the live
docs — fix those ⚠️ VERIFY constants and re-run until this passes.

--test-order will place a MARKET order for a tiny notional and then immediately
close it with a reduce-only market order. It refuses to run in live mode.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


async def main():
    testnet = os.environ.get("EXECUTION_MODE", "testnet").strip().lower() != "live"
    want_order = "--test-order" in sys.argv

    from tools.yubit_client import YubitBroker, symbol_for
    broker = YubitBroker(testnet=testnet)
    env = "TESTNET" if testnet else "LIVE"
    print(f"\n=== Yubit self-test ({env}) — base {broker.base} ===\n")

    if not broker.configured():
        print(f"✗ No API key/secret for {env}. Set "
              f"{'YUBIT_TESTNET_API_KEY/SECRET' if testnet else 'YUBIT_API_KEY/SECRET'} in .env.")
        return 1

    print("1. ping (server time)...", end=" ", flush=True)
    ok = await broker.ping()
    print("✓" if ok else "✗ FAILED — check BASE_URL and EP_SERVER_TIME")
    if not ok:
        return 1

    print("2. auth + balance...", end=" ", flush=True)
    bal = await broker.get_balance()
    if bal is None:
        print("✗ FAILED — signing/auth or EP_BALANCE shape is wrong. Fix the ⚠️ "
              "VERIFY constants in tools/yubit_client.py and re-run.")
        return 1
    print(f"✓  USDT balance: {bal:,.2f}")

    print("3. open positions...", end=" ", flush=True)
    pos = await broker.get_open_positions()
    print(f"✓  {len(pos)} open")
    for p in pos:
        print(f"     {p}")

    if not want_order:
        print("\nRead-only checks passed. Re-run with --test-order to test a real "
              "(testnet) order round-trip.\n")
        return 0

    if not testnet:
        print("\n✗ Refusing --test-order in LIVE mode. Set EXECUTION_MODE=testnet.\n")
        return 1

    # ── tiny open + flatten on testnet ──
    from tools.broker import OrderRequest
    asset = os.environ.get("YUBIT_SELFTEST_ASSET", "BTC")
    from tools.market_prices import get_price
    px = await get_price(asset, "crypto") or 0
    if px <= 0:
        print(f"\n✗ Could not fetch a reference price for {asset}.\n")
        return 1
    notional = float(os.environ.get("YUBIT_SELFTEST_NOTIONAL", "20"))
    units = round(notional / px, 6)

    print(f"\n4. TEST ORDER: open {units} {asset} (~${notional:.0f}) MARKET on testnet...")
    req = OrderRequest(asset=asset, direction="LONG", units=units, leverage=2,
                       entry=px, order_type="MARKET", stop_loss=None,
                       take_profits=[], client_id="selftest")
    res = await broker.open_position(req)
    print(f"   -> ok={res.ok} id={res.order_id} fill={res.avg_fill_price} err={res.error}")
    for w in res.warnings:
        print(f"      • {w}")
    if not res.ok:
        return 1

    print("5. flatten (reduce-only market close)...", end=" ", flush=True)
    from tools.yubit_client import EP_ORDER
    ok2, data2, err2 = await broker._request(
        EP_ORDER,
        {"symbol": symbol_for(asset), "side": "SELL", "type": "MARKET",
         "quantity": units, "reduceOnly": "true"},
        signed=True)
    print("✓ closed" if ok2 else f"✗ FAILED to close — flatten manually! ({err2})")
    print("\nDone.\n")
    return 0 if ok2 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
