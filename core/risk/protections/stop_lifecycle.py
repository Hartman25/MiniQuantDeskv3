"""
PATCH 9 — Authoritative protective-stop lifecycle manager.

Centralises the create / cancel / restore lifecycle of protective stop
orders so that there is exactly one source of truth for which stops are
active.  Every lifecycle event produces a journal-ready dict for durable
audit.

PROBLEM SOLVED:
    Previously, protective_stop_ids was a bare in-memory dict in app.py
    with no durable tracking.  On crash/restart the dict was lost, and
    recovery relied on a best-effort broker query.

    StopLifecycleManager keeps the same {symbol → stop_id} mapping but
    adds:
      - Explicit lifecycle events (PLACED, CANCELLED, RESTORED)
      - Idempotent place/cancel (no-op on duplicate)
      - restore_from_events() for crash recovery from event log
      - Thread safety
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StopLifecycleEvent:
    """Journal-ready record of a protective-stop lifecycle transition."""
    event: str           # "protective_stop_placed" | "protective_stop_cancelled" | ...
    symbol: str
    stop_order_id: Optional[str]
    entry_order_id: Optional[str]
    timestamp: str       # ISO-8601
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event": self.event,
            "symbol": self.symbol,
            "stop_order_id": self.stop_order_id,
            "entry_order_id": self.entry_order_id,
            "timestamp": self.timestamp,
        }
        d.update(self.details)
        return d


class StopLifecycleManager:
    """
    Authoritative tracker for protective stop orders.

    Thread-safe.  All mutations go through ``place()`` and ``cancel()``
    which return ``StopLifecycleEvent`` dicts the caller should log.

    Example::

        mgr = StopLifecycleManager()
        ev = mgr.place("SPY", stop_id="BRK-42", entry_order_id="ORD-7")
        journal.write_event(ev.to_dict())
        ...
        ev = mgr.cancel("SPY")
        journal.write_event(ev.to_dict())
    """

    def __init__(self) -> None:
        self._stops: Dict[str, Dict[str, Any]] = {}  # symbol → info
        self._lock = threading.Lock()
        self._history: List[StopLifecycleEvent] = []

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_stop_id(self, symbol: str) -> Optional[str]:
        """Return the broker stop-order ID for *symbol*, or None."""
        with self._lock:
            info = self._stops.get(symbol)
            return info["stop_order_id"] if info else None

    def has_stop(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._stops

    def active_stops(self) -> Dict[str, str]:
        """Return snapshot {symbol: stop_order_id} of all active stops."""
        with self._lock:
            return {sym: info["stop_order_id"] for sym, info in self._stops.items()}

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._stops)

    @property
    def history(self) -> List[StopLifecycleEvent]:
        """Full lifecycle history (for diagnostics/tests)."""
        with self._lock:
            return list(self._history)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def place(
        self,
        symbol: str,
        *,
        stop_order_id: str,
        entry_order_id: Optional[str] = None,
        stop_price: Optional[str] = None,
    ) -> StopLifecycleEvent:
        """
        Record that a protective stop has been placed for *symbol*.

        Idempotent: if a stop already exists for the symbol the call
        returns a ``protective_stop_already_exists`` event and does NOT
        overwrite.
        """
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            if symbol in self._stops:
                ev = StopLifecycleEvent(
                    event="protective_stop_already_exists",
                    symbol=symbol,
                    stop_order_id=self._stops[symbol]["stop_order_id"],
                    entry_order_id=entry_order_id,
                    timestamp=now,
                    details={"attempted_stop_id": stop_order_id},
                )
                self._history.append(ev)
                return ev

            info: Dict[str, Any] = {
                "stop_order_id": stop_order_id,
                "entry_order_id": entry_order_id,
                "stop_price": stop_price,
                "placed_at": now,
            }
            self._stops[symbol] = info

            ev = StopLifecycleEvent(
                event="protective_stop_placed",
                symbol=symbol,
                stop_order_id=stop_order_id,
                entry_order_id=entry_order_id,
                timestamp=now,
                details={"stop_price": stop_price} if stop_price else {},
            )
            self._history.append(ev)
            return ev

    def cancel(
        self,
        symbol: str,
        *,
        reason: str = "exit_signal",
    ) -> StopLifecycleEvent:
        """
        Record that the protective stop for *symbol* has been cancelled.

        Idempotent: if no stop exists returns a ``protective_stop_not_found``
        event (no error raised).
        """
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            info = self._stops.pop(symbol, None)

            if info is None:
                ev = StopLifecycleEvent(
                    event="protective_stop_not_found",
                    symbol=symbol,
                    stop_order_id=None,
                    entry_order_id=None,
                    timestamp=now,
                    details={"reason": reason},
                )
                self._history.append(ev)
                return ev

            ev = StopLifecycleEvent(
                event="protective_stop_cancelled",
                symbol=symbol,
                stop_order_id=info["stop_order_id"],
                entry_order_id=info.get("entry_order_id"),
                timestamp=now,
                details={"reason": reason},
            )
            self._history.append(ev)
            return ev

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def restore_from_events(self, events: List[Dict[str, Any]]) -> int:
        """
        Replay lifecycle events (from journal or transaction log) to
        reconstruct the active-stop map.

        Only ``protective_stop_placed`` and ``protective_stop_cancelled``
        events are relevant.  The final state of each symbol after replay
        determines whether a stop is considered active.

        Returns the number of active stops after replay.
        """
        # Build per-symbol timeline
        symbol_state: Dict[str, Optional[Dict[str, Any]]] = {}

        for ev in events:
            et = ev.get("event") or ev.get("event_type") or ""
            sym = ev.get("symbol")
            if not sym:
                continue

            if et == "protective_stop_placed":
                symbol_state[sym] = {
                    "stop_order_id": ev.get("stop_order_id", ""),
                    "entry_order_id": ev.get("entry_order_id"),
                    "stop_price": ev.get("stop_price"),
                    "placed_at": ev.get("timestamp"),
                }
            elif et in ("protective_stop_cancelled", "protective_stop_filled"):
                symbol_state[sym] = None  # removed

        # Apply final state
        restored = 0
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            for sym, info in symbol_state.items():
                if info is not None and sym not in self._stops:
                    self._stops[sym] = info
                    self._history.append(
                        StopLifecycleEvent(
                            event="protective_stop_restored",
                            symbol=sym,
                            stop_order_id=info["stop_order_id"],
                            entry_order_id=info.get("entry_order_id"),
                            timestamp=now,
                        )
                    )
                    restored += 1

        return restored
