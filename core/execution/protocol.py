"""
PATCH 13 — Unified execution interface (Protocol).

Defines ``ExecutionProtocol`` — the set of methods that every
execution backend (live, paper, backtest, mock) must implement so
that strategy code, runtime, and tests all program against one shape.

This is a structural sub-typing Protocol (PEP 544), so existing
classes satisfy it as long as they have the right methods.  No
inheritance required.

MINIMUM VIABLE INTERFACE:
    submit_market_order(...)  →  str          # broker_order_id
    submit_limit_order(...)   →  str
    submit_stop_order(...)    →  str
    cancel_order(...)         →  bool
    get_order_status(...)     →  OrderStatus
    get_fill_details(...)     →  (qty, price)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class ExecutionProtocol(Protocol):
    """
    Structural interface that every execution backend must satisfy.

    This is a ``runtime_checkable`` Protocol — you can use
    ``isinstance(obj, ExecutionProtocol)`` to verify compliance at
    runtime (duck-typing friendly, no inheritance needed).

    Backends:
      - ``OrderExecutionEngine``  (live / paper)
      - ``SimulatedBroker`` or adapter (backtest)
      - ``MockExecution``  (unit tests)
    """

    def submit_market_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        """Submit a market order.  Returns broker_order_id."""
        ...

    def submit_limit_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        limit_price: Decimal,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        """Submit a limit order.  Returns broker_order_id."""
        ...

    def submit_stop_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        stop_price: Decimal,
        strategy: str,
    ) -> str:
        """Submit a stop order.  Returns broker_order_id."""
        ...

    def cancel_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        reason: str = "cancelled",
    ) -> bool:
        """Cancel an order.  Returns True if cancel was accepted."""
        ...

    def get_order_status(
        self,
        internal_order_id: str,
        broker_order_id: str,
    ) -> str:
        """Return current status string for the order."""
        ...

    def get_fill_details(
        self,
        internal_order_id: str,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Return (filled_qty, fill_price) or (None, None)."""
        ...


class NullExecution:
    """
    No-op execution backend for dry-run / testing.

    Satisfies ``ExecutionProtocol`` but never submits real orders.
    Useful for signal-generation testing, strategy validation,
    and paper-trade smoke tests where no broker is available.
    """

    def __init__(self) -> None:
        self._next_id = 0
        self._orders: dict[str, dict] = {}

    def _gen_id(self) -> str:
        self._next_id += 1
        return f"NULL-{self._next_id}"

    def submit_market_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        bid = self._gen_id()
        self._orders[internal_order_id] = {
            "broker_order_id": bid, "symbol": symbol, "side": side,
            "quantity": quantity, "type": "MARKET", "status": "FILLED",
            "filled_qty": quantity, "fill_price": Decimal("100"),
        }
        return bid

    def submit_limit_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        limit_price: Decimal,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        bid = self._gen_id()
        self._orders[internal_order_id] = {
            "broker_order_id": bid, "symbol": symbol, "side": side,
            "quantity": quantity, "type": "LIMIT", "status": "SUBMITTED",
            "limit_price": limit_price, "filled_qty": None, "fill_price": None,
        }
        return bid

    def submit_stop_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        stop_price: Decimal,
        strategy: str,
    ) -> str:
        bid = self._gen_id()
        self._orders[internal_order_id] = {
            "broker_order_id": bid, "symbol": symbol, "side": side,
            "quantity": quantity, "type": "STOP", "status": "SUBMITTED",
            "stop_price": stop_price, "filled_qty": None, "fill_price": None,
        }
        return bid

    def cancel_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        reason: str = "cancelled",
    ) -> bool:
        order = self._orders.get(internal_order_id)
        if order:
            order["status"] = "CANCELLED"
            return True
        return False

    def get_order_status(
        self,
        internal_order_id: str,
        broker_order_id: str,
    ) -> str:
        order = self._orders.get(internal_order_id)
        return order["status"] if order else "UNKNOWN"

    def get_fill_details(
        self,
        internal_order_id: str,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        order = self._orders.get(internal_order_id)
        if order:
            return order.get("filled_qty"), order.get("fill_price")
        return None, None
