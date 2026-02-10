"""
Deterministic chaos broker stub for torture tests.

Simulates:
- Market closed/open transitions on a per-cycle schedule
- Clock API failures (ConnectionError, TimeoutError)
- Order submission failures on a seeded deterministic schedule
- Safe dummy returns for account/position/order queries

All randomness uses random.Random(seed) for reproducibility.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


class ChaosBroker:
    """Broker stub with deterministic failure injection.

    Parameters
    ----------
    seed : int
        Seed for the RNG used for failure scheduling.
    closed_until_cycle : int
        Cycles 0..closed_until_cycle-1 report market as CLOSED.
        Cycle closed_until_cycle onwards reports OPEN.
    clock_error_cycles : set[int]
        Cycle numbers where get_clock() raises ConnectionError.
    order_fail_cycles : set[int]
        Cycle numbers where submit_*_order raises an exception.
    order_fail_exc : type
        Exception class to raise on order_fail_cycles.
    next_open_utc : datetime | None
        The next_open time reported while market is closed.
    """

    def __init__(
        self,
        *,
        seed: int = 42,
        closed_until_cycle: int = 5,
        clock_error_cycles: Optional[set] = None,
        order_fail_cycles: Optional[set] = None,
        order_fail_exc: type = ConnectionError,
        next_open_utc: Optional[datetime] = None,
    ) -> None:
        self.paper = True
        self._rng = random.Random(seed)
        self._cycle = 0
        self._closed_until = closed_until_cycle
        self._clock_error_cycles = clock_error_cycles or set()
        self._order_fail_cycles = order_fail_cycles or set()
        self._order_fail_exc = order_fail_exc
        self._next_open = next_open_utc or datetime(2026, 2, 9, 14, 30, 0, tzinfo=timezone.utc)

        # Tracking
        self.clock_calls: List[int] = []
        self.order_submissions: List[Dict[str, Any]] = []
        self.account_calls: int = 0
        self.position_calls: int = 0

    # -- Clock ---------------------------------------------------------------

    def get_clock(self) -> Dict[str, Any]:
        """Return market clock; raise on configured error cycles."""
        cycle = self._cycle
        self.clock_calls.append(cycle)

        if cycle in self._clock_error_cycles:
            raise ConnectionError(f"simulated clock failure at cycle {cycle}")

        is_open = cycle >= self._closed_until
        return {
            "is_open": is_open,
            "timestamp": datetime.now(timezone.utc),
            "next_open": None if is_open else self._next_open,
            "next_close": (datetime.now(timezone.utc) + timedelta(hours=6)) if is_open else None,
        }

    # -- Account & positions -------------------------------------------------

    def get_account_info(self) -> Dict[str, Any]:
        self.account_calls += 1
        return {
            "portfolio_value": Decimal("100000"),
            "buying_power": Decimal("50000"),
            "cash": Decimal("50000"),
            "pattern_day_trader": False,
        }

    def get_positions(self) -> list:
        self.position_calls += 1
        return []

    def list_positions(self) -> list:
        return self.get_positions()

    def list_open_orders(self) -> list:
        return []

    def get_orders(self, status: str = "open") -> list:
        return []

    # -- Order submission ----------------------------------------------------

    def submit_market_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: Any,
        internal_order_id: str,
    ) -> str:
        self._maybe_fail_order("submit_market_order")
        record = {
            "method": "submit_market_order",
            "cycle": self._cycle,
            "symbol": symbol,
            "quantity": quantity,
            "side": str(side),
            "internal_order_id": internal_order_id,
        }
        self.order_submissions.append(record)
        return f"chaos-mkt-{internal_order_id}"

    def submit_limit_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: Any,
        limit_price: Decimal,
        internal_order_id: str,
    ) -> str:
        self._maybe_fail_order("submit_limit_order")
        record = {
            "method": "submit_limit_order",
            "cycle": self._cycle,
            "symbol": symbol,
            "quantity": quantity,
            "side": str(side),
            "limit_price": limit_price,
            "internal_order_id": internal_order_id,
        }
        self.order_submissions.append(record)
        return f"chaos-lmt-{internal_order_id}"

    def submit_stop_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: Any,
        stop_price: Decimal,
        internal_order_id: str,
    ) -> str:
        self._maybe_fail_order("submit_stop_order")
        record = {
            "method": "submit_stop_order",
            "cycle": self._cycle,
            "symbol": symbol,
            "quantity": quantity,
            "side": str(side),
            "stop_price": stop_price,
            "internal_order_id": internal_order_id,
        }
        self.order_submissions.append(record)
        return f"chaos-stp-{internal_order_id}"

    # -- Order queries -------------------------------------------------------

    def get_order_status(self, broker_order_id: str) -> Tuple:
        from core.state import OrderStatus
        return OrderStatus.FILLED, {
            "filled_qty": Decimal("1"),
            "filled_avg_price": Decimal("100.00"),
        }

    def cancel_order(self, broker_order_id: str) -> bool:
        return True

    # -- Cycle management (called by harness) --------------------------------

    def advance_cycle(self) -> None:
        """Advance internal cycle counter. Called by the run harness."""
        self._cycle += 1

    @property
    def cycle(self) -> int:
        return self._cycle

    # -- Internals -----------------------------------------------------------

    def _maybe_fail_order(self, method: str) -> None:
        if self._cycle in self._order_fail_cycles:
            raise self._order_fail_exc(
                f"simulated {method} failure at cycle {self._cycle}"
            )


class TransientChaosBroker(ChaosBroker):
    """ChaosBroker variant with seeded random transient failures.

    Instead of specifying exact failure cycles, failures are generated
    probabilistically from the seed.
    """

    def __init__(
        self,
        *,
        seed: int = 42,
        clock_fail_prob: float = 0.15,
        order_fail_prob: float = 0.2,
        total_cycles: int = 50,
        closed_until_cycle: int = 0,
        next_open_utc: Optional[datetime] = None,
    ) -> None:
        rng = random.Random(seed)

        clock_error_cycles = {
            c for c in range(total_cycles) if rng.random() < clock_fail_prob
        }
        order_fail_cycles = {
            c for c in range(total_cycles) if rng.random() < order_fail_prob
        }

        # Pick random exception types per failure from a transient set
        self._transient_exceptions = [
            ConnectionError,
            TimeoutError,
            OSError,
        ]

        super().__init__(
            seed=seed,
            closed_until_cycle=closed_until_cycle,
            clock_error_cycles=clock_error_cycles,
            order_fail_cycles=order_fail_cycles,
            order_fail_exc=ConnectionError,  # default; overridden per-call
            next_open_utc=next_open_utc,
        )
        self._fail_rng = random.Random(seed + 1)

    def _maybe_fail_order(self, method: str) -> None:
        if self._cycle in self._order_fail_cycles:
            exc_cls = self._fail_rng.choice(self._transient_exceptions)
            raise exc_cls(f"transient {method} failure at cycle {self._cycle}")
