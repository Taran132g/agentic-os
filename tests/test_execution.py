"""
Unit tests for the Dr. Profit auto-executor's deterministic surface.

Network + LLM paths (Claude parse, price fetch, live orders) are validated
separately by yubit_selftest.py; these tests cover the pure logic that decides
whether and how big an order gets placed.
"""

import asyncio

import pytest

from tools.exec_config import load_config, MODE_DRY_RUN
from tools.broker import OrderRequest, PaperBroker
from tools import yubit_client
import execution_workflow as ew


# ── config ───────────────────────────────────────────────────────────────────

def _clear_exec_env(monkeypatch):
    for k in ("EXECUTION_MODE", "DR_PROFIT_AUTO_EXECUTE", "EXEC_KILL_SWITCH",
              "EXEC_MAX_NOTIONAL_USD", "EXEC_MAX_LEVERAGE", "EXEC_ALLOWED_ASSETS",
              "EXEC_MIN_CONFIDENCE", "EXEC_RISK_GATE_BLOCKS"):
        monkeypatch.delenv(k, raising=False)


def test_config_defaults_are_safe(monkeypatch):
    _clear_exec_env(monkeypatch)
    cfg = load_config()
    assert cfg.mode == MODE_DRY_RUN
    assert cfg.auto_execute is False
    assert cfg.kill_switch is False
    # Disarmed by default: auto-execute off is the resting reason.
    assert cfg.gate_reason() == "auto-execute is OFF (DR_PROFIT_AUTO_EXECUTE=0)"


def test_invalid_mode_falls_back_to_dry_run(monkeypatch):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "banana")
    assert load_config().mode == MODE_DRY_RUN


def test_kill_switch_takes_precedence(monkeypatch):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("DR_PROFIT_AUTO_EXECUTE", "1")
    monkeypatch.setenv("EXEC_KILL_SWITCH", "1")
    cfg = load_config()
    assert cfg.is_live
    assert cfg.gate_reason() == "kill switch is ON (EXEC_KILL_SWITCH=1)"


def test_armed_config_has_no_gate_reason(monkeypatch):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "testnet")
    monkeypatch.setenv("DR_PROFIT_AUTO_EXECUTE", "1")
    assert load_config().gate_reason() is None


def test_allowed_assets_parsing(monkeypatch):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("EXEC_ALLOWED_ASSETS", " btc, eth ,sol ")
    assert load_config().allowed_assets == frozenset({"BTC", "ETH", "SOL"})


# ── OrderRequest side mapping ────────────────────────────────────────────────

def test_order_sides_long():
    req = OrderRequest(asset="BTC", direction="LONG", units=1, leverage=2,
                       entry=100, order_type="MARKET", stop_loss=90)
    assert req.side == "BUY" and req.close_side == "SELL"


def test_order_sides_short():
    req = OrderRequest(asset="BTC", direction="SHORT", units=1, leverage=2,
                       entry=100, order_type="MARKET", stop_loss=110)
    assert req.side == "SELL" and req.close_side == "BUY"


# ── PaperBroker ──────────────────────────────────────────────────────────────

def test_paper_broker_fills_at_entry_and_marks_protected():
    req = OrderRequest(asset="ETH", direction="LONG", units=0.5, leverage=3,
                       entry=2000, order_type="MARKET", stop_loss=1900,
                       client_id="drp-abc")
    res = asyncio.run(PaperBroker().open_position(req))
    assert res.ok and res.mode == "dry_run"
    assert res.avg_fill_price == 2000 and res.filled_units == 0.5
    assert res.protected is True


def test_paper_broker_unprotected_without_stop():
    req = OrderRequest(asset="ETH", direction="LONG", units=1, leverage=1,
                       entry=2000, order_type="MARKET", stop_loss=None)
    res = asyncio.run(PaperBroker().open_position(req))
    assert res.ok and res.protected is False


# ── dedupe / signal hashing ──────────────────────────────────────────────────

def test_signal_hash_is_stable_and_field_sensitive():
    a = {"asset": "BTC", "direction": "LONG", "entry": 65000, "take_profits": [67000]}
    b = dict(a)
    c = dict(a, direction="SHORT")
    assert ew._signal_hash("dr_profit", a) == ew._signal_hash("dr_profit", b)
    assert ew._signal_hash("dr_profit", a) != ew._signal_hash("dr_profit", c)


def test_recent_duplicate_window():
    from datetime import datetime, timezone
    h = "deadbeef"
    fresh = {"executions": [{"hash": h, "ts": datetime.now(timezone.utc).isoformat()}]}
    old = {"executions": [{"hash": h, "ts": "2000-01-01T00:00:00+00:00"}]}
    assert ew._recent_duplicate(fresh, h) is True
    assert ew._recent_duplicate(old, h) is False
    assert ew._recent_duplicate(fresh, "other") is False


# ── executor gating (no network: returns before parse) ───────────────────────

def test_execute_signal_blocked_when_disarmed(monkeypatch):
    _clear_exec_env(monkeypatch)  # auto-execute off by default
    res = asyncio.run(ew.execute_signal("BTC long entry 65000 sl 64000", "dr_profit"))
    assert res["executed"] is False
    assert res["status"] == "blocked"
    assert "auto-execute is OFF" in res["reason"]


def test_execute_signal_blocked_by_kill_switch(monkeypatch):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("DR_PROFIT_AUTO_EXECUTE", "1")
    monkeypatch.setenv("EXEC_KILL_SWITCH", "1")
    res = asyncio.run(ew.execute_signal("BTC long entry 65000", "dr_profit"))
    assert res["status"] == "blocked" and "kill switch" in res["reason"]


# ── Yubit signing / symbol shape ─────────────────────────────────────────────

def test_symbol_format():
    assert yubit_client.symbol_for("btc") == "BTCUSDT"
    assert yubit_client.symbol_for(" eth ") == "ETHUSDT"


def test_signed_query_shape(monkeypatch):
    monkeypatch.setenv("YUBIT_TESTNET_API_KEY", "k")
    monkeypatch.setenv("YUBIT_TESTNET_API_SECRET", "supersecret")
    broker = yubit_client.YubitBroker(testnet=True)
    assert broker.configured()
    q = broker._signed_query({"symbol": "BTCUSDT", "side": "BUY"})
    # HMAC-SHA256 hex signature is 64 chars; params + ts + recvWindow present.
    assert "signature=" in q and "timestamp=" in q and "recvWindow=" in q
    sig = q.split("signature=")[1]
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_unconfigured_broker_reports_error():
    # No keys set -> signed request returns an error, never raises.
    broker = yubit_client.YubitBroker(testnet=True)
    broker.key = ""
    broker.secret = ""
    ok, data, err = asyncio.run(broker._request(yubit_client.EP_BALANCE, signed=True))
    assert ok is False and "not set" in err


# ── alert rendering ──────────────────────────────────────────────────────────

def test_format_exec_block_variants():
    blocked = ew.format_exec_block({"status": "blocked", "reason": "kill switch is ON"})
    assert "not placed" in blocked and "kill switch" in blocked

    placed = ew.format_exec_block({
        "status": "placed", "mode": "testnet", "protected": True,
        "order": {"units": 0.01, "fill": 65000, "leverage": 5, "notional": 650,
                  "order_id": "X1"},
        "warnings": [],
    })
    assert "TESTNET" in placed and "stop-loss set" in placed and "X1" in placed

    unprot = ew.format_exec_block({
        "status": "placed", "mode": "live", "protected": False,
        "order": {"units": 1, "fill": 10, "leverage": 1, "notional": 10},
        "warnings": ["⚠️ STOP-LOSS FAILED"],
    })
    assert "NO STOP-LOSS" in unprot


# ── full orchestration (LLM + price network stubbed) ─────────────────────────

def _arm_dry_run(monkeypatch, tmp_path):
    _clear_exec_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "dry_run")
    monkeypatch.setenv("DR_PROFIT_AUTO_EXECUTE", "1")
    monkeypatch.setenv("EXEC_MAX_NOTIONAL_USD", "100000")  # don't clamp in this test
    monkeypatch.setattr(ew, "STATE_FILE", tmp_path / "execution_state.json")
    monkeypatch.setattr(ew, "_open_auto_positions", lambda: 0)
    monkeypatch.setattr(ew, "_today_auto_realized_loss", lambda: 0.0)

    # Redirect the trade tracker to a temp file so paper recording never touches
    # the real trades.json.
    import tools.trade_tracker as tt
    monkeypatch.setattr(tt, "TRADES_FILE", tmp_path / "trades.json")

    import live_signal_workflow as lsw
    import risk_gate_workflow as rg
    import tools.position_sizer as ps

    async def fake_parse(text, risk_usd, broadcast=None):
        return {"asset": "BTC", "asset_class": "crypto", "direction": "LONG",
                "entry": 65000, "stop_loss": 64000, "take_profits": [67000, 69000],
                "leverage": 5, "confidence": 90, "note": "clean setup"}

    async def fake_size(**kw):
        return {"ok": True, "risk_usd": 60, "entry": 65000, "stop_loss": 64000,
                "stop_source": "signal", "stop_pct": 1.54, "units": 0.06,
                "notional": 3900, "leverage": 5, "margin": 780, "rr_to_tp1": 3.0,
                "annual_vol": 0.5, "expected_move_pct": 4.0, "hold_days": 3,
                "exit_plan": {"tp1_frac": 0.34}, "warnings": []}

    async def fake_gate(sig, broadcast=None):
        return {"verdict": "APPROVE", "confidence": 80, "suggested_risk_pct": 20,
                "reasoning": "ok", "debate_summary": ""}

    async def pass_classify(text, broadcast=None):   # cheap gate passes by default
        return {"is_signal": True, "confidence": 90, "direction": "LONG"}

    monkeypatch.setattr(lsw, "_classify_signal", pass_classify)
    monkeypatch.setattr(lsw, "_claude_parse", fake_parse)
    monkeypatch.setattr(ps, "size_trade", fake_size)
    monkeypatch.setattr(rg, "evaluate_signal", fake_gate)


def test_full_dry_run_places_and_dedupes(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    text = "BTC LONG entry 65000 sl 64000 tp 67000"

    res = asyncio.run(ew.execute_signal(text, "dr_profit"))
    assert res["executed"] is True and res["status"] == "placed"
    assert res["mode"] == "dry_run"
    assert res["protected"] is True                 # stop-loss present
    assert res["order"]["units"] == pytest.approx(0.06)
    assert res["order"]["leverage"] == 5

    # Dry-run now records a PAPER trade to the tracker (marked, not real).
    assert res["trade_id"] is not None
    import tools.trade_tracker as tt
    recorded = [t for t in tt.get_active_trades() if t["id"] == res["trade_id"]]
    assert recorded and recorded[0]["source"] == "dr_profit_paper"
    assert recorded[0].get("extra", {}).get("paper") is True

    # Second identical signal within the window is deduped.
    res2 = asyncio.run(ew.execute_signal(text, "dr_profit"))
    assert res2["executed"] is False and "duplicate" in res2["reason"]


def test_leverage_capped_in_dry_run(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_MAX_LEVERAGE", "3")     # below the signal's 5x
    res = asyncio.run(ew.execute_signal("BTC LONG 65000", "dr_profit"))
    assert res["executed"] is True
    assert res["order"]["leverage"] == 3             # clamped down


def test_notional_clamped_in_dry_run(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_MAX_NOTIONAL_USD", "1000")  # 0.06*65000=3900 -> clamp
    res = asyncio.run(ew.execute_signal("BTC LONG 65000", "dr_profit"))
    assert res["executed"] is True
    assert res["order"]["notional"] == pytest.approx(1000, abs=1)
    assert res["clamp_note"] and "clamp" in res["clamp_note"].lower()


def test_risk_gate_reject_blocks(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    import risk_gate_workflow as rg

    async def reject(sig, broadcast=None):
        return {"verdict": "REJECT", "confidence": 70, "suggested_risk_pct": 0,
                "reasoning": "overexposed", "debate_summary": ""}

    monkeypatch.setattr(rg, "evaluate_signal", reject)
    res = asyncio.run(ew.execute_signal("BTC LONG 65000", "dr_profit"))
    assert res["executed"] is False and "REJECT" in res["reason"]


def test_low_confidence_skipped(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    import live_signal_workflow as lsw

    async def weak(text, risk_usd, broadcast=None):
        return {"asset": "BTC", "asset_class": "crypto", "direction": "LONG",
                "entry": 65000, "stop_loss": 64000, "take_profits": [],
                "leverage": 1, "confidence": 20, "note": "chatter"}

    monkeypatch.setattr(lsw, "_claude_parse", weak)
    res = asyncio.run(ew.execute_signal("maybe btc goes up?", "dr_profit"))
    assert res["executed"] is False and "confidence" in res["reason"]


# ── entry-type intent: "he bought at X" -> MARKET, "limit at X" -> LIMIT ──────

from live_signal_workflow import _normalize, _infer_entry_type   # noqa: E402


def test_infer_entry_type_market_when_already_bought():
    assert _infer_entry_type("I bought BTC at 65000", 65000) == "market"
    assert _infer_entry_type("just longed ETH here", None) == "market"


def test_infer_entry_type_limit_when_resting_order():
    assert _infer_entry_type("BTC buy limit 64000", 64000) == "limit"
    assert _infer_entry_type("set a buy at 3.20 on retest", 3.20) == "limit"


def test_infer_entry_type_defaults():
    assert _infer_entry_type("BTC target 70000", 65000) == "limit"   # price, no intent word
    assert _infer_entry_type("thinking BTC looks good", None) == "market"  # no price


def test_normalize_passes_and_defaults_entry_type():
    base = {"asset": "BTC", "direction": "LONG", "confidence": 80}
    assert _normalize({**base, "entry": 65000, "entry_type": "market"})["entry_type"] == "market"
    assert _normalize({**base, "entry": 64000, "entry_type": "limit"})["entry_type"] == "limit"
    assert _normalize({**base, "entry": 64000})["entry_type"] == "limit"     # price -> limit
    assert _normalize({**base, "entry": None})["entry_type"] == "market"     # no price -> market


def _echo_size_factory(mark):
    async def echo_size(**kw):
        entry = kw["entry"] if kw["entry"] is not None else mark
        return {"ok": True, "risk_usd": 60, "entry": entry, "stop_loss": 64000,
                "stop_source": "signal", "stop_pct": 5.0, "units": 0.01,
                "notional": entry * 0.01, "leverage": kw["leverage"], "margin": 100,
                "rr_to_tp1": 2.0, "annual_vol": 0.5, "expected_move_pct": 4.0,
                "hold_days": 3, "exit_plan": {"tp1_frac": 0.34}, "warnings": []}
    return echo_size


def test_market_entry_follows_current_price(monkeypatch, tmp_path):
    """He says 'I bought at 65000' but price moved to 72000 -> we MARKET in at 72000."""
    _arm_dry_run(monkeypatch, tmp_path)
    import live_signal_workflow as lsw
    import tools.position_sizer as ps

    async def market_parse(text, risk_usd, broadcast=None):
        return {"asset": "BTC", "asset_class": "crypto", "direction": "LONG",
                "entry": 65000, "entry_type": "market", "stop_loss": 64000,
                "take_profits": [], "leverage": 3, "confidence": 90, "note": "already in"}

    monkeypatch.setattr(lsw, "_claude_parse", market_parse)
    monkeypatch.setattr(ps, "size_trade", _echo_size_factory(mark=72000))
    res = asyncio.run(ew.execute_signal("I bought BTC at 65000", "dr_profit"))
    assert res["executed"] is True
    assert res["order"]["fill"] == pytest.approx(72000)   # current price, not his 65000


def test_limit_entry_rests_at_his_price(monkeypatch, tmp_path):
    """He says 'buy limit 64000' -> we rest a LIMIT at 64000, ignoring the 72000 mark."""
    _arm_dry_run(monkeypatch, tmp_path)
    import live_signal_workflow as lsw
    import tools.position_sizer as ps

    async def limit_parse(text, risk_usd, broadcast=None):
        return {"asset": "BTC", "asset_class": "crypto", "direction": "LONG",
                "entry": 64000, "entry_type": "limit", "stop_loss": 63000,
                "take_profits": [], "leverage": 2, "confidence": 90, "note": "buy limit"}

    monkeypatch.setattr(lsw, "_claude_parse", limit_parse)
    monkeypatch.setattr(ps, "size_trade", _echo_size_factory(mark=72000))
    res = asyncio.run(ew.execute_signal("BTC buy limit 64000", "dr_profit"))
    assert res["executed"] is True
    assert res["order"]["fill"] == pytest.approx(64000)   # his limit, not the mark


def _stock_parse():
    async def parse(text, risk_usd, broadcast=None):
        return {"asset": "COIN", "asset_class": "stock", "direction": "LONG",
                "entry": 168, "entry_type": "market", "stop_loss": 150,
                "take_profits": [], "leverage": 1, "confidence": 80, "note": "stock pick"}
    return parse


def test_stock_skipped_for_real_orders(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "live")   # Yubit = crypto only
    import live_signal_workflow as lsw
    monkeypatch.setattr(lsw, "_claude_parse", _stock_parse())
    res = asyncio.run(ew.execute_signal("bought COIN at 168", "dr_profit"))
    assert res["executed"] is False and "crypto-only" in res["reason"]


def test_stock_allowed_in_dry_run(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    import live_signal_workflow as lsw
    import tools.position_sizer as ps
    monkeypatch.setattr(lsw, "_claude_parse", _stock_parse())
    monkeypatch.setattr(ps, "size_trade", _echo_size_factory(mark=170))
    res = asyncio.run(ew.execute_signal("bought COIN at 168", "dr_profit"))
    assert res["executed"] is True and res["mode"] == "dry_run"


# ── tiered LLM: cheap classify gate must filter before the pricey extraction ──

def test_classify_gate_skips_without_extraction(monkeypatch, tmp_path):
    _arm_dry_run(monkeypatch, tmp_path)
    import live_signal_workflow as lsw

    async def chatter(text, broadcast=None):
        return {"is_signal": False, "confidence": 10, "direction": "none"}

    async def boom(*a, **k):
        raise AssertionError("expensive extraction ran despite classify reject")

    monkeypatch.setattr(lsw, "_classify_signal", chatter)
    monkeypatch.setattr(lsw, "_claude_parse", boom)
    res = asyncio.run(ew.execute_signal("being bullish is more fun than bearish", "dr_profit"))
    assert res["executed"] is False and "classifier" in res["reason"]


def test_llm_api_model_tiers(monkeypatch):
    for k in ("PAIS_LLM_MODEL_CLASSIFY", "PAIS_LLM_MODEL_EXTRACT",
              "PAIS_LLM_MODEL_RISK", "PAIS_LLM_API_MODEL"):
        monkeypatch.delenv(k, raising=False)
    import importlib
    from tools import llm_api
    importlib.reload(llm_api)
    assert "haiku" in llm_api._model_for("classify")        # cheap gate
    assert "sonnet" in llm_api._model_for("live_trader")    # signal extraction
    assert "sonnet" in llm_api._model_for("risk_gate")      # risk reasoning
    assert llm_api._model_for("something_else") == llm_api.DEFAULT_MODEL
