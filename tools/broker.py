"""
Broker abstraction — a normalized order interface so the executor is
exchange-agnostic and testable without a network.

Concrete brokers:
  - PaperBroker : places nothing; used for DRY_RUN. Returns a synthetic fill at
                  the intended entry so the rest of the pipeline can run.
  - YubitBroker : tools/yubit_client.py — real REST orders (testnet or live).

All money math happens BEFORE a broker is called (tools/position_sizer.py). A
broker only translates a normalized OrderRequest into exchange calls and returns
a normalized OrderResult. Brokers must NEVER raise to the caller — they return
OrderResult(ok=False, error=...) so the executor always gets a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OrderRequest:
    asset: str                  # bare ticker, e.g. "BTC"
    direction: str              # "LONG" | "SHORT"
    units: float                # base-asset quantity to open
    leverage: int
    entry: float                # intended entry (limit price, or market reference)
    order_type: str             # "MARKET" | "LIMIT"
    stop_loss: Optional[float]
    take_profits: list = field(default_factory=list)
    tp1_fraction: Optional[float] = None   # fraction of size to close at TP1
    client_id: str = ""                    # idempotency key echoed to the exchange

    @property
    def side(self) -> str:
        """Exchange side for the OPENING order."""
        return "BUY" if self.direction.upper() == "LONG" else "SELL"

    @property
    def close_side(self) -> str:
        """Side that reduces/closes the position (for SL/TP orders)."""
        return "SELL" if self.direction.upper() == "LONG" else "BUY"


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    mode: str                                  # dry_run | testnet | live
    order_id: Optional[str] = None
    avg_fill_price: Optional[float] = None
    filled_units: Optional[float] = None
    stop_order_id: Optional[str] = None
    tp_order_ids: list = field(default_factory=list)
    protected: bool = False                    # True only if a stop-loss is confirmed live
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    raw: Optional[dict] = None


class Broker:
    """Interface. Subclasses set `mode` and implement the async methods."""

    mode = "abstract"

    def configured(self) -> bool:
        return True

    async def ping(self) -> bool:
        raise NotImplementedError

    async def get_balance(self) -> Optional[float]:
        raise NotImplementedError

    async def open_position(self, req: OrderRequest) -> OrderResult:
        raise NotImplementedError

    async def get_open_positions(self) -> list:
        raise NotImplementedError


class PaperBroker(Broker):
    """No-network broker for DRY_RUN. Simulates a clean fill at the intended entry."""

    mode = "dry_run"

    def configured(self) -> bool:
        return True

    async def ping(self) -> bool:
        return True

    async def get_balance(self) -> Optional[float]:
        return None

    async def open_position(self, req: OrderRequest) -> OrderResult:
        return OrderResult(
            ok             = True,
            mode           = self.mode,
            order_id       = f"paper-{req.client_id or 'sim'}",
            avg_fill_price = req.entry,
            filled_units   = req.units,
            stop_order_id  = f"paper-sl-{req.client_id or 'sim'}" if req.stop_loss else None,
            protected      = req.stop_loss is not None,
            raw            = {"note": "DRY_RUN — no order was placed on any exchange"},
        )

    async def get_open_positions(self) -> list:
        return []
