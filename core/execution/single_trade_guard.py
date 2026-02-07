"""
PATCH 11 — Single-trade-at-a-time engine-level guard.

Provides an **atomic** check-and-reserve mechanism so that at most one
non-exit order per symbol can be in-flight at any time.  This closes the
race window between the coordinator's pure check and actual broker
submission.

DESIGN:
    guard.try_reserve(symbol, order_id)  →  True  (reserved)
    guard.try_reserve(symbol, order_id2) →  False (blocked)
    guard.release(symbol)                →  slot freed

Thread-safe.  Every reserve/release/block produces a journal-ready event.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SingleTradeEvent:
    """Journal-ready record of a single-trade guard decision."""
    event: str          # "single_trade_reserved" | "single_trade_blocked" | "single_trade_released"
    symbol: str
    order_id: Optional[str]
    timestamp: str      # ISO-8601
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event": self.event,
            "symbol": self.symbol,
            "order_id": self.order_id,
            "timestamp": self.timestamp,
        }
        d.update(self.details)
        return d


class SingleTradeGuard:
    """
    Atomic single-trade-at-a-time guard.

    This guard ensures that at most one entry order per symbol can be
    in-flight.  The guard is thread-safe and every decision is logged.

    Usage::

        guard = SingleTradeGuard()

        # Before submitting an entry order
        ev = guard.try_reserve("SPY", "ORD-1")
        if ev.event == "single_trade_reserved":
            submit_order(...)
        else:
            skip_signal(...)  # blocked

        # After fill or cancel
        guard.release("SPY")

    Exit orders (SELL) should NOT go through this guard — they bypass it.
    """

    def __init__(self) -> None:
        self._reserved: Dict[str, str] = {}  # symbol → order_id
        self._lock = threading.Lock()
        self._history: List[SingleTradeEvent] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def try_reserve(self, symbol: str, order_id: str) -> SingleTradeEvent:
        """
        Atomically attempt to reserve *symbol* for *order_id*.

        Returns:
            SingleTradeEvent with event="single_trade_reserved" on success,
            or event="single_trade_blocked" if the symbol is already taken.
        """
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            existing = self._reserved.get(symbol)
            if existing is not None:
                ev = SingleTradeEvent(
                    event="single_trade_blocked",
                    symbol=symbol,
                    order_id=order_id,
                    timestamp=now,
                    details={"blocking_order_id": existing},
                )
                self._history.append(ev)
                return ev

            self._reserved[symbol] = order_id
            ev = SingleTradeEvent(
                event="single_trade_reserved",
                symbol=symbol,
                order_id=order_id,
                timestamp=now,
            )
            self._history.append(ev)
            return ev

    def release(self, symbol: str, *, reason: str = "completed") -> SingleTradeEvent:
        """
        Release the reservation for *symbol*.

        Idempotent: releasing an unreserved symbol returns a no-op event.
        """
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            removed = self._reserved.pop(symbol, None)

            if removed is None:
                ev = SingleTradeEvent(
                    event="single_trade_release_noop",
                    symbol=symbol,
                    order_id=None,
                    timestamp=now,
                    details={"reason": reason},
                )
                self._history.append(ev)
                return ev

            ev = SingleTradeEvent(
                event="single_trade_released",
                symbol=symbol,
                order_id=removed,
                timestamp=now,
                details={"reason": reason},
            )
            self._history.append(ev)
            return ev

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_reserved(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._reserved

    def get_reservation(self, symbol: str) -> Optional[str]:
        """Return the order_id holding the reservation, or None."""
        with self._lock:
            return self._reserved.get(symbol)

    def reserved_symbols(self) -> Dict[str, str]:
        """Snapshot of {symbol: order_id} for all reservations."""
        with self._lock:
            return dict(self._reserved)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._reserved)

    @property
    def history(self) -> List[SingleTradeEvent]:
        with self._lock:
            return list(self._history)

    # ------------------------------------------------------------------
    # Bulk operations (for startup / reconciliation)
    # ------------------------------------------------------------------

    def restore_reservations(self, reservations: Dict[str, str]) -> int:
        """
        Restore reservations from a persisted snapshot (e.g. after restart).
        Skips symbols already reserved.  Returns count of newly restored.
        """
        restored = 0
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            for symbol, order_id in reservations.items():
                if symbol not in self._reserved:
                    self._reserved[symbol] = order_id
                    self._history.append(
                        SingleTradeEvent(
                            event="single_trade_restored",
                            symbol=symbol,
                            order_id=order_id,
                            timestamp=now,
                        )
                    )
                    restored += 1

        return restored

    def clear_all(self) -> int:
        """Clear all reservations.  Returns count cleared."""
        with self._lock:
            count = len(self._reserved)
            self._reserved.clear()
            return count
