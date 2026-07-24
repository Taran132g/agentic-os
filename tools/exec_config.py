"""
Execution config — single source of truth for the live-trade executor.

Every knob is an environment variable so the same code runs in DRY_RUN on the
Mac and LIVE on Oracle with only .env differing. Safe by default: with nothing
set the executor is in DRY_RUN, auto-execute is OFF, and the kill switch is
checked on every call. Three independent things must ALL be true for a real
order to fire:
    1. EXECUTION_MODE=testnet|live   (not dry_run)
    2. DR_PROFIT_AUTO_EXECUTE=1       (auto-execute on)
    3. EXEC_KILL_SWITCH=0             (kill switch off)
...plus valid API keys for the chosen mode. Miss any one and nothing is placed.
"""

import os
from dataclasses import dataclass

MODE_DRY_RUN = "dry_run"   # full pipeline, logs the intended order, places nothing
MODE_TESTNET = "testnet"   # real orders on the Yubit TESTNET (fake funds)
MODE_LIVE    = "live"      # real orders with real money
_VALID_MODES = {MODE_DRY_RUN, MODE_TESTNET, MODE_LIVE}


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip())
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, "").strip()))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ExecConfig:
    mode: str
    auto_execute: bool
    kill_switch: bool
    max_notional_usd: float      # per-trade notional cap; 0 = no cap; oversized clamp DOWN
    max_leverage: int            # leverage is clamped to this regardless of signal
    max_concurrent: int          # max simultaneous auto-opened positions; 0 = unlimited
    max_trades_per_day: int      # per-UTC-day execution count cap
    daily_loss_stop_usd: float   # halt auto-exec once today's realized loss exceeds this
    min_confidence: int          # min agent confidence (0-100) to act on a signal
    allowed_assets: frozenset    # empty = allow all; else only these tickers
    risk_gate_blocks: bool       # a REJECT verdict from the risk gate blocks execution

    @property
    def is_dry_run(self) -> bool:
        return self.mode == MODE_DRY_RUN

    @property
    def is_testnet(self) -> bool:
        return self.mode == MODE_TESTNET

    @property
    def is_live(self) -> bool:
        return self.mode == MODE_LIVE

    def gate_reason(self) -> str | None:
        """Why execution is globally disabled right now, or None if armed."""
        if self.kill_switch:
            return "kill switch is ON (EXEC_KILL_SWITCH=1)"
        if not self.auto_execute:
            return "auto-execute is OFF (DR_PROFIT_AUTO_EXECUTE=0)"
        return None


def load_config() -> ExecConfig:
    """Read the executor config fresh from the environment. Never raises."""
    mode = (os.environ.get("EXECUTION_MODE", MODE_DRY_RUN) or MODE_DRY_RUN).strip().lower()
    if mode not in _VALID_MODES:
        mode = MODE_DRY_RUN

    assets_raw = (os.environ.get("EXEC_ALLOWED_ASSETS", "") or "").strip()
    allowed = frozenset(a.strip().upper() for a in assets_raw.split(",") if a.strip())

    return ExecConfig(
        mode                = mode,
        auto_execute        = _env_bool("DR_PROFIT_AUTO_EXECUTE", False),
        kill_switch         = _env_bool("EXEC_KILL_SWITCH", False),
        max_notional_usd    = _env_float("EXEC_MAX_NOTIONAL_USD", 100.0),
        max_leverage        = _env_int("EXEC_MAX_LEVERAGE", 10),
        max_concurrent      = _env_int("EXEC_MAX_CONCURRENT", 3),
        max_trades_per_day  = _env_int("EXEC_MAX_TRADES_PER_DAY", 10),
        daily_loss_stop_usd = _env_float("EXEC_DAILY_LOSS_STOP_USD", 200.0),
        min_confidence      = _env_int("EXEC_MIN_CONFIDENCE", 55),
        allowed_assets      = allowed,
        risk_gate_blocks    = _env_bool("EXEC_RISK_GATE_BLOCKS", True),
    )
