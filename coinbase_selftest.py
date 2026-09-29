"""
Coinbase US futures self-test — run the moment the CDP key is installed, BEFORE
arming the auto-executor. Coinbase has no testnet, so nothing here trades:

    python3.11 coinbase_selftest.py             # products + auth + balance + positions + fee tier
    python3.11 coinbase_selftest.py --preview   # + PREVIEW a 1-contract maker entry with bracket
                                                #   (Coinbase validates it; nothing is executed)
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DR_PROFIT_CORE = ["BTC", "ETH", "SOL", "XRP", "LINK", "AVAX", "DOGE", "ONDO", "AAVE", "ADA"]


async def _preview(broker, products: dict) -> int:
    from tools.coinbase_futures_client import round_to_tick
    asset = os.environ.get("COINBASE_SELFTEST_ASSET", "ETH")
    prod = products[asset]
    pid, tick = prod["product_id"], prod["tick"]
    bid = await broker._touch(pid, "BUY")
    if not bid:
        print(f"✗ no bid for {pid}")
        return 1
    order = {"limit_limit_gtc": {"base_size": "1", "limit_price": round_to_tick(bid, tick),
                                 "post_only": True}}
    bracket = {"trigger_bracket_gtc": {"limit_price": round_to_tick(bid * 1.10, tick),
                                       "stop_trigger_price": round_to_tick(bid * 0.95, tick)}}
    print(f"\n5. PREVIEW (not executed): BUY 1 {pid} post-only @ {bid:g}, "
          f"TP +10% / stop -5%...")
    try:
        r = await broker._call("preview_order", product_id=pid, side="BUY",
                               order_configuration=order,
                               attached_order_configuration=bracket)
    except Exception as e:
        print(f"   ✗ preview call failed: {type(e).__name__}: {e}")
        return 1
    errs = r.get("errs") or []
    for k in ("order_total", "commission_total", "leverage", "est_average_filled_price",
              "predicted_liquidation_price", "slippage"):
        if r.get(k) not in (None, ""):
            print(f"   {k}: {r[k]}")
    if errs:
        print(f"   ✗ Coinbase would REJECT this order: {errs}")
        return 1
    print("   ✓ Coinbase accepts this order shape (maker entry + attached bracket)")
    return 0


async def main() -> int:
    from tools.coinbase_futures_client import CoinbaseFuturesBroker, perp_products
    broker = CoinbaseFuturesBroker()
    print("\n=== Coinbase US futures self-test (LIVE account, read-only) ===\n")

    print("1. perp-style products (public)...", end=" ", flush=True)
    try:
        products = perp_products(await broker._call(
            "get_public_products", product_type="FUTURE", get_all_products=True))
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}")
        return 1
    print(f"✓ {len(products)} crypto perps")
    print("   Dr. Profit core coins: " + ", ".join(
        f"{a}{'✓' if a in products else '✗'}" for a in DR_PROFIT_CORE))

    if not broker.configured():
        print("\n✗ No key. Run ./set_coinbase_key.sh ~/Downloads/cdp_api_key.json")
        return 1

    print("2. auth + futures balance...", end=" ", flush=True)
    bal = await broker.get_balance()
    if bal is None:
        print("✗ FAILED — key invalid, not scoped to the DEFAULT portfolio, or futures "
              "not enabled on this account")
        return 1
    print(f"✓ ${bal:,.2f}")

    print("3. open futures positions...", end=" ", flush=True)
    pos = await broker.get_open_positions()
    print(f"✓ {len(pos)} open")
    for p in pos:
        print(f"     {p}")

    print("4. your futures fee tier...", end=" ", flush=True)
    fee = await broker.get_fee_tier()
    if fee:
        print(f"✓ {fee['tier']}: maker {fee['maker'] * 100:.3f}%  taker {fee['taker'] * 100:.3f}%")
    else:
        print("? unavailable (non-fatal)")

    if "--preview" in sys.argv:
        return await _preview(broker, products)
    print("\nRead-only checks passed. Re-run with --preview to validate an order shape.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
