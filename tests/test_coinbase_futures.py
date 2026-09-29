"""
Unit tests for the Coinbase US futures (CFM perp-style) broker adapter.

The SDK client is replaced by an in-memory fake so these run offline. Coinbase
has no testnet: the real auth/balance/fee-tier path and an order PREVIEW (no
execution) are validated by coinbase_selftest.py.
"""

import asyncio

import pytest

from tools.broker import OrderRequest
from tools import coinbase_futures_client as cb

PRODUCTS = {"products": [
    {"product_id": "BIP-20DEC30-CDE", "display_name": "BTC PERP", "quote_increment": "0.01", "price_increment": "5",
     "future_product_details": {"contract_root_unit": "BTC", "contract_size": "0.01",
                                "display_name": "Bitcoin Perpetual"}},
    {"product_id": "ETP-20DEC30-CDE", "display_name": "ETH PERP", "quote_increment": "0.01", "price_increment": "0.5",
     "future_product_details": {"contract_root_unit": "ETH", "contract_size": "0.1",
                                "display_name": "Ethereum Perpetual"}},
    {"product_id": "BIT-31OCT26-CDE", "display_name": "BTC OCT",  "quote_increment": "0.01", "price_increment": "5",
     "future_product_details": {"contract_root_unit": "BTC", "contract_size": "0.01",
                                "display_name": "Bitcoin Oct 2026"}},
    {"product_id": "US5-19DEC30-CDE", "display_name": "US500", "quote_increment": "0.01", "price_increment": "0.1",
     "future_product_details": {"contract_root_unit": "CDEUS5", "contract_size": "1",
                                "display_name": "US 500 Perpetual"}},
    {"product_id": "SHP-20DEC30-CDE", "display_name": "1000SHIB", "quote_increment": "0.01", "price_increment": "0.000001",
     "future_product_details": {"contract_root_unit": "SHIB", "contract_size": "10000",
                                "display_name": "1000SHIB Perpetual"}},
]}


class FakeClient:
    """Mimics the coinbase.rest.RESTClient surface the adapter uses (sync, dict responses)."""

    def __init__(self, fills=None, reject=None):
        self.orders = []            # create_order payloads, in order
        self.cancelled = []
        # fills: per created order index -> list of successive get_order statuses
        self.fills = fills or {}
        self.reject = reject or {}  # created order index -> error code

    def get_public_products(self, product_type=None, get_all_products=False):
        assert product_type == "FUTURE"
        return PRODUCTS

    def get_best_bid_ask(self, product_ids=None):
        return {"pricebooks": [{"product_id": product_ids[0],
                                "bids": [{"price": "64990"}], "asks": [{"price": "65010"}]}]}

    def create_order(self, client_order_id, product_id, side, order_configuration, **kw):
        idx = len(self.orders)
        self.orders.append({"client_order_id": client_order_id, "product_id": product_id,
                            "side": side, "config": order_configuration, **kw})
        if idx in self.reject:
            return {"success": False, "error_response": {"error": self.reject[idx],
                                                          "message": "rejected"}}
        return {"success": True, "success_response": {"order_id": f"o{idx}"}}

    def get_order(self, order_id):
        idx = int(order_id[1:])
        seq = self.fills.get(idx, [("FILLED", None)])
        status, filled = seq.pop(0) if len(seq) > 1 else seq[0]
        size = self.orders[idx]["config"]
        size = next(iter(size.values()))["base_size"]
        return {"order": {"order_id": order_id, "status": status,
                          "filled_size": filled if filled is not None else
                          (size if status == "FILLED" else "0"),
                          "average_filled_price": "65000" if status == "FILLED" else "0"}}

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)
        return {"results": [{"success": True, "order_id": o} for o in order_ids]}

    def get_futures_balance_summary(self):
        return {"balance_summary": {"total_usd_balance": {"value": "1500.25"},
                                    "futures_buying_power": {"value": "900"}}}

    def list_futures_positions(self):
        return {"positions": [{"product_id": "BIP-20DEC30-CDE", "side": "LONG",
                               "number_of_contracts": "2", "avg_entry_price": "64000"}]}

    def get_transaction_summary(self, product_type=None):
        return {"fee_tier": {"pricing_tier": "Intro", "maker_fee_rate": "0",
                             "taker_fee_rate": "0.0003"}}


def _broker(client, style="maker_first", wait=0.0):
    b = cb.CoinbaseFuturesBroker(client_factory=lambda: client, entry_style=style,
                                 maker_wait_secs=wait, poll_secs=0.0)
    b.key, b.secret = "organizations/x/apiKeys/y", "-----BEGIN EC PRIVATE KEY-----"
    return b


def _req(**over):
    base = dict(asset="BTC", direction="LONG", units=0.0234, leverage=5, entry=65000.0,
                order_type="MARKET", stop_loss=62000.0, take_profits=[70000.0, 75000.0],
                tp1_fraction=0.5, client_id="drp-abc")
    base.update(over)
    return OrderRequest(**base)


def _run(coro):
    return asyncio.run(coro)


# ── product resolution + sizing ──────────────────────────────────────────────

def test_perp_map_keeps_only_crypto_perpetuals():
    m = cb.perp_products(PRODUCTS)
    assert set(m) == {"BTC", "ETH"}                  # no dated, index, or 1000x products
    assert m["BTC"]["product_id"] == "BIP-20DEC30-CDE"
    assert m["BTC"]["contract_size"] == 0.01


def test_contracts_round_down_never_up():
    assert cb.contracts_for(0.0234, 0.01) == 2      # 2.34 -> 2, never 3 (risk only shrinks)
    assert cb.contracts_for(0.0099, 0.01) == 0


def test_price_rounds_to_tick():
    assert cb.round_to_tick(65003.2, 5) == "65005"
    assert cb.round_to_tick(2674.26, 0.5) == "2674.5"


# ── order placement ─────────────────────────────────────────────────────────

def test_market_signal_goes_maker_first_at_touch_with_attached_bracket():
    client = FakeClient()
    res = _run(_broker(client).open_position(_req()))

    assert res.ok and res.protected and res.filled_units == pytest.approx(0.02)
    (entry,) = client.orders
    cfg = entry["config"]["limit_limit_gtc"]
    assert entry["side"] == "BUY" and entry["product_id"] == "BIP-20DEC30-CDE"
    assert cfg == {"base_size": "2", "limit_price": "64990", "post_only": True}  # joins the bid
    br = entry["attached_order_configuration"]["trigger_bracket_gtc"]
    assert br == {"limit_price": "70000", "stop_trigger_price": "62000"}
    assert "leverage" not in entry                   # US futures leverage is set by margin
    assert any("maker" in w for w in res.warnings)


def test_unfilled_maker_falls_back_to_taker_market():
    client = FakeClient(fills={0: [("OPEN", "0")]})
    res = _run(_broker(client).open_position(_req()))
    assert res.ok and res.protected
    assert client.cancelled == ["o0"]
    maker, taker = client.orders
    assert "market_market_ioc" in taker["config"]
    assert taker["config"]["market_market_ioc"]["base_size"] == "2"
    assert "trigger_bracket_gtc" in taker["attached_order_configuration"]
    assert taker["client_order_id"] != maker["client_order_id"]


def test_partial_maker_fill_keeps_partial_and_does_not_chase():
    client = FakeClient(fills={0: [("OPEN", "1")]})
    res = _run(_broker(client).open_position(_req(units=0.05)))
    assert res.ok and res.filled_units == pytest.approx(0.01)
    assert len(client.orders) == 1 and client.cancelled == ["o0"]
    assert any("partial" in w for w in res.warnings)


def test_maker_only_skips_when_unfilled():
    client = FakeClient(fills={0: [("OPEN", "0")]})
    res = _run(_broker(client, style="maker_only").open_position(_req()))
    assert res.ok is False and "not filled" in res.error
    assert len(client.orders) == 1


def test_taker_style_sends_market_directly():
    client = FakeClient()
    res = _run(_broker(client, style="taker").open_position(_req()))
    assert res.ok
    (entry,) = client.orders
    assert "market_market_ioc" in entry["config"]


def test_limit_signal_rests_post_only_at_signal_price():
    client = FakeClient(fills={0: [("OPEN", "0")]})
    res = _run(_broker(client).open_position(
        _req(direction="SHORT", order_type="LIMIT", entry=66001.0, stop_loss=68000.0,
             take_profits=[60000.0])))
    assert res.ok and res.protected
    (entry,) = client.orders                          # resting — no cancel, no chase
    assert entry["side"] == "SELL"
    assert entry["config"]["limit_limit_gtc"] == {"base_size": "2", "limit_price": "66000",
                                                  "post_only": True}
    assert client.cancelled == []
    assert any("resting" in w for w in res.warnings)


def test_crossed_limit_price_is_treated_as_market():
    client = FakeClient(reject={0: "INVALID_LIMIT_PRICE_POST_ONLY"})
    res = _run(_broker(client).open_position(_req(order_type="LIMIT", entry=66000.0)))
    assert res.ok
    assert client.orders[1]["config"]["limit_limit_gtc"]["limit_price"] == "64990"


def test_no_take_profit_uses_far_target_so_stop_still_attaches():
    client = FakeClient()
    res = _run(_broker(client).open_position(_req(take_profits=[])))
    assert res.ok and res.protected
    br = client.orders[0]["attached_order_configuration"]["trigger_bracket_gtc"]
    assert float(br["limit_price"]) == pytest.approx(65000 + 5 * 3000, abs=5)
    assert any("no take-profit" in w for w in res.warnings)


def test_no_stop_opens_unprotected_with_warning():
    client = FakeClient()
    res = _run(_broker(client).open_position(_req(stop_loss=None)))
    assert res.ok and not res.protected
    assert "attached_order_configuration" not in client.orders[0]
    assert any("UNPROTECTED" in w or "without a hard stop" in w for w in res.warnings)


def test_size_below_one_contract_is_refused_before_ordering():
    client = FakeClient()
    res = _run(_broker(client).open_position(_req(units=0.005)))
    assert res.ok is False and "1 contract" in res.error
    assert client.orders == []


def test_unlisted_asset_is_refused():
    client = FakeClient()
    res = _run(_broker(client).open_position(_req(asset="ONDO")))
    assert res.ok is False and "no Coinbase US perp" in res.error


def test_rejected_entry_is_reported():
    client = FakeClient(reject={0: "INSUFFICIENT_FUND"})
    res = _run(_broker(client, style="taker").open_position(_req()))
    assert res.ok is False and "INSUFFICIENT_FUND" in res.error


# ── reads + config ──────────────────────────────────────────────────────────

def test_balance_positions_and_fee_tier():
    b = _broker(FakeClient())
    assert _run(b.get_balance()) == 1500.25
    assert _run(b.get_open_positions()) == [{"symbol": "BIP-20DEC30-CDE", "side": "LONG",
                                             "contracts": 2.0, "entryPrice": 64000.0}]
    assert _run(b.get_fee_tier()) == {"tier": "Intro", "maker": 0.0, "taker": 0.0003}


def test_unconfigured_broker_refuses(monkeypatch):
    for k in ("COINBASE_API_KEY", "COINBASE_API_SECRET", "COINBASE_KEY_FILE"):
        monkeypatch.delenv(k, raising=False)
    b = cb.CoinbaseFuturesBroker()
    assert not b.configured()
    res = _run(b.open_position(_req()))
    assert res.ok is False and "not set" in res.error


def test_key_file_counts_as_configured(monkeypatch, tmp_path):
    f = tmp_path / "cdp_api_key.json"
    f.write_text('{"name": "organizations/x/apiKeys/y", "privateKey": "pem"}')
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.setenv("COINBASE_KEY_FILE", str(f))
    assert cb.CoinbaseFuturesBroker().configured()


def test_executor_routes_coinbase_and_refuses_testnet(monkeypatch):
    import execution_workflow as ew
    from tools.exec_config import load_config
    monkeypatch.setenv("EXEC_BROKER", "coinbase")
    monkeypatch.setenv("EXECUTION_MODE", "testnet")
    broker, err = ew._pick_broker(load_config())
    assert broker is None and "no testnet" in err
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("COINBASE_API_KEY", "k")
    monkeypatch.setenv("COINBASE_API_SECRET", "s")
    broker, err = ew._pick_broker(load_config())
    assert err is None and isinstance(broker, cb.CoinbaseFuturesBroker)
