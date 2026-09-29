"""
Unit tests for the Kraken Futures broker adapter.

The exchange is replaced by an in-memory fake so these run offline. The real
round-trip (auth, balance, open + flatten) is validated by kraken_selftest.py
against Kraken's demo environment.
"""

import asyncio

import pytest

from tools.broker import OrderRequest
from tools import kraken_futures_client as kf


class FakeExchange:
    """Records every call; mimics the ccxt.async_support.krakenfutures surface we use."""

    def __init__(self, fail_on=None, markets=None):
        self.calls = []
        self.fail_on = set(fail_on or [])
        self.markets = markets if markets is not None else {
            "BTC/USD:USD": {"id": "PF_XBTUSD", "precision": {"amount": 0.0001}},
            "ETH/USD:USD": {"id": "PF_ETHUSD", "precision": {"amount": 0.001}},
        }
        self.closed = False

    async def load_markets(self):
        self.calls.append(("load_markets",))
        return self.markets

    def amount_to_precision(self, symbol, amount):
        step = self.markets[symbol]["precision"]["amount"]
        steps = int(round(amount / step, 9))                  # truncate, float-safe
        if steps <= 0:
            raise ValueError("amount below precision")          # ccxt raises InvalidOrder here
        return f"{steps * step:.10f}"

    async def set_leverage(self, leverage, symbol):
        self.calls.append(("set_leverage", leverage, symbol))
        if "set_leverage" in self.fail_on:
            raise RuntimeError("leverage rejected")

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        params = params or {}
        kind = ("stop" if "stopLossPrice" in params
                else "tp" if "takeProfitPrice" in params else "entry")
        self.calls.append(("create_order", kind, symbol, type, side, amount, price, params))
        if kind in self.fail_on:
            raise RuntimeError(f"{kind} rejected")
        return {"id": f"{kind}-{len(self.calls)}", "average": 65010.0 if kind == "entry" else None}

    async def fetch_balance(self):
        return {"total": {"USD": 1234.5}}

    async def fetch_positions(self):
        return [{"symbol": "BTC/USD:USD", "contracts": 0.01, "side": "long", "entryPrice": 65000},
                {"symbol": "ETH/USD:USD", "contracts": 0, "side": "long", "entryPrice": None}]

    async def close(self):
        self.closed = True


def _broker(fake):
    b = kf.KrakenFuturesBroker(demo=True, exchange_factory=lambda: fake)
    b.key, b.secret = "k", "s"
    return b


def _req(**over):
    base = dict(asset="BTC", direction="LONG", units=0.01234, leverage=5, entry=65000.0,
                order_type="MARKET", stop_loss=62000.0, take_profits=[70000.0, 75000.0],
                tp1_fraction=0.5, client_id="drp-abc")
    base.update(over)
    return OrderRequest(**base)


def _orders(fake, kind):
    return [c for c in fake.calls if c[0] == "create_order" and c[1] == kind]


def test_symbol_for_maps_to_usd_linear_perp():
    assert kf.symbol_for("btc") == "BTC/USD:USD"
    assert kf.symbol_for(" eth ") == "ETH/USD:USD"


def test_unconfigured_broker_refuses_without_network(monkeypatch):
    monkeypatch.delenv("KRAKEN_FUTURES_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_FUTURES_DEMO_API_SECRET", raising=False)
    b = kf.KrakenFuturesBroker(demo=True)
    assert not b.configured()
    res = asyncio.run(b.open_position(_req()))
    assert res.ok is False and "not set" in res.error


def test_keys_read_per_mode(monkeypatch):
    monkeypatch.setenv("KRAKEN_FUTURES_API_KEY", "live-k")
    monkeypatch.setenv("KRAKEN_FUTURES_API_SECRET", "live-s")
    monkeypatch.setenv("KRAKEN_FUTURES_DEMO_API_KEY", "demo-k")
    monkeypatch.setenv("KRAKEN_FUTURES_DEMO_API_SECRET", "demo-s")
    assert kf.KrakenFuturesBroker(demo=False).key == "live-k"
    assert kf.KrakenFuturesBroker(demo=True).key == "demo-k"
    assert kf.KrakenFuturesBroker(demo=True).mode == "testnet"


def test_open_position_places_entry_stop_and_split_tps():
    fake = FakeExchange()
    res = asyncio.run(_broker(fake).open_position(_req()))

    assert res.ok and res.protected
    assert res.avg_fill_price == 65010.0
    assert res.filled_units == pytest.approx(0.0123)          # rounded DOWN to 0.0001 step
    assert ("set_leverage", 5, "BTC/USD:USD") in fake.calls

    (entry,) = _orders(fake, "entry")
    assert entry[3:6] == ("market", "buy", pytest.approx(0.0123))
    assert entry[7]["clientOrderId"] == "drp-abc"

    (stop,) = _orders(fake, "stop")
    assert stop[4] == "sell" and stop[7]["stopLossPrice"] == 62000.0
    assert stop[7]["reduceOnly"] is True and stop[5] == pytest.approx(0.0123)

    tps = _orders(fake, "tp")
    assert [t[7]["takeProfitPrice"] for t in tps] == [70000.0, 75000.0]
    assert sum(t[5] for t in tps) == pytest.approx(0.0123)    # TPs never exceed position
    assert tps[0][5] == pytest.approx(0.0061, abs=1e-4)
    assert fake.closed


def test_short_limit_order_uses_limit_price_and_buy_side_protection():
    fake = FakeExchange()
    res = asyncio.run(_broker(fake).open_position(
        _req(direction="SHORT", order_type="LIMIT", entry=66000.0,
             stop_loss=68000.0, take_profits=[60000.0], tp1_fraction=None)))
    assert res.ok
    (entry,) = _orders(fake, "entry")
    assert entry[3:5] == ("limit", "sell") and entry[6] == 66000.0
    assert _orders(fake, "stop")[0][4] == "buy"
    (tp,) = _orders(fake, "tp")
    assert tp[5] == pytest.approx(0.0123)                     # single TP closes it all


def test_failed_stop_leaves_position_flagged_unprotected():
    fake = FakeExchange(fail_on={"stop"})
    res = asyncio.run(_broker(fake).open_position(_req(take_profits=[])))
    assert res.ok and not res.protected
    assert any("UNPROTECTED" in w for w in res.warnings)


def test_rejected_entry_places_no_protection_orders():
    fake = FakeExchange(fail_on={"entry"})
    res = asyncio.run(_broker(fake).open_position(_req()))
    assert res.ok is False and "entry order rejected" in res.error
    assert not _orders(fake, "stop") and not _orders(fake, "tp")
    assert fake.closed


def test_unlisted_asset_is_rejected_before_any_order():
    fake = FakeExchange()
    res = asyncio.run(_broker(fake).open_position(_req(asset="NOPE")))
    assert res.ok is False and "not listed" in res.error
    assert not [c for c in fake.calls if c[0] == "create_order"]


def test_size_below_contract_step_is_refused():
    fake = FakeExchange()
    res = asyncio.run(_broker(fake).open_position(_req(units=0.00001)))
    assert res.ok is False and "quantity" in res.error


def test_leverage_failure_is_a_warning_not_a_block():
    fake = FakeExchange(fail_on={"set_leverage"})
    res = asyncio.run(_broker(fake).open_position(_req()))
    assert res.ok and any("leverage" in w for w in res.warnings)


def test_balance_and_positions_read():
    fake = FakeExchange()
    b = _broker(fake)
    assert asyncio.run(b.get_balance()) == 1234.5
    pos = asyncio.run(b.get_open_positions())
    assert pos == [{"symbol": "BTC/USD:USD", "contracts": 0.01, "side": "long",
                    "entryPrice": 65000}]


# ── executor wiring ──────────────────────────────────────────────────────────

def test_executor_defaults_to_kraken_and_reports_missing_keys(monkeypatch):
    import execution_workflow as ew
    from tools.exec_config import load_config
    for k in ("EXEC_BROKER", "KRAKEN_FUTURES_DEMO_API_KEY", "KRAKEN_FUTURES_DEMO_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EXECUTION_MODE", "testnet")
    cfg = load_config()
    assert cfg.broker == "kraken_futures"
    broker, err = ew._pick_broker(cfg)
    assert broker is None and "Kraken Futures TESTNET" in err


def test_executor_picks_configured_kraken_demo_broker(monkeypatch):
    import execution_workflow as ew
    from tools.exec_config import load_config
    monkeypatch.setenv("EXECUTION_MODE", "testnet")
    monkeypatch.setenv("KRAKEN_FUTURES_DEMO_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_FUTURES_DEMO_API_SECRET", "s")
    broker, err = ew._pick_broker(load_config())
    assert err is None and isinstance(broker, kf.KrakenFuturesBroker)
    assert broker.mode == "testnet"


def test_yubit_still_selectable(monkeypatch):
    import execution_workflow as ew
    from tools.exec_config import load_config
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("EXEC_BROKER", "yubit")
    monkeypatch.delenv("YUBIT_API_KEY", raising=False)
    broker, err = ew._pick_broker(load_config())
    assert broker is None and "Yubit LIVE" in err


def test_real_factory_targets_demo_host_in_testnet():
    pytest.importorskip("ccxt")
    ex = kf._default_exchange_factory("k", "s", demo=True)()
    try:
        assert "demo-futures.kraken.com" in ex.urls["api"]["private"]
    finally:
        asyncio.run(ex.close())
    live = kf._default_exchange_factory("k", "s", demo=False)()
    try:
        assert live.urls["api"]["private"].startswith("https://futures.kraken.com")
    finally:
        asyncio.run(live.close())
