"""
PATCH 11 — Guarantee single-trade-at-a-time enforced in engine

INVARIANT:
    At the engine level, at most one entry order per symbol may be
    in-flight at any time.  The SingleTradeGuard provides an atomic
    check-and-reserve that closes the race window between the
    coordinator's pure check and actual broker submission.

TESTS:
    1.  try_reserve succeeds on empty guard.
    2.  try_reserve blocked when symbol already reserved.
    3.  release frees the symbol for re-reservation.
    4.  release on unreserved symbol is idempotent (noop).
    5.  Different symbols can be reserved concurrently.
    6.  is_reserved / get_reservation queries work.
    7.  reserved_symbols snapshot.
    8.  count property.
    9.  Event to_dict has required keys.
   10.  history records all events.
   11.  Thread safety: concurrent try_reserve only one wins.
   12.  restore_reservations from snapshot.
   13.  restore_reservations skips existing.
   14.  clear_all removes all reservations.
   15.  Blocked event carries blocking_order_id.
"""

import threading
import pytest

from core.execution.single_trade_guard import (
    SingleTradeEvent,
    SingleTradeGuard,
)


class TestSingleTradeGuard:

    def test_reserve_succeeds_on_empty(self):
        guard = SingleTradeGuard()
        ev = guard.try_reserve("SPY", "ORD-1")
        assert ev.event == "single_trade_reserved"
        assert ev.symbol == "SPY"
        assert ev.order_id == "ORD-1"

    def test_reserve_blocked_when_taken(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        ev = guard.try_reserve("SPY", "ORD-2")
        assert ev.event == "single_trade_blocked"
        assert ev.order_id == "ORD-2"

    def test_release_frees_symbol(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        ev_rel = guard.release("SPY")
        assert ev_rel.event == "single_trade_released"

        ev2 = guard.try_reserve("SPY", "ORD-2")
        assert ev2.event == "single_trade_reserved"

    def test_release_idempotent(self):
        guard = SingleTradeGuard()
        ev = guard.release("AAPL")
        assert ev.event == "single_trade_release_noop"

    def test_different_symbols_concurrent(self):
        guard = SingleTradeGuard()
        ev1 = guard.try_reserve("SPY", "ORD-1")
        ev2 = guard.try_reserve("AAPL", "ORD-2")
        assert ev1.event == "single_trade_reserved"
        assert ev2.event == "single_trade_reserved"

    def test_is_reserved_and_get_reservation(self):
        guard = SingleTradeGuard()
        assert guard.is_reserved("SPY") is False
        assert guard.get_reservation("SPY") is None

        guard.try_reserve("SPY", "ORD-1")
        assert guard.is_reserved("SPY") is True
        assert guard.get_reservation("SPY") == "ORD-1"

    def test_reserved_symbols_snapshot(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        guard.try_reserve("AAPL", "ORD-2")

        snap = guard.reserved_symbols()
        assert snap == {"SPY": "ORD-1", "AAPL": "ORD-2"}

    def test_count_property(self):
        guard = SingleTradeGuard()
        assert guard.count == 0
        guard.try_reserve("SPY", "ORD-1")
        assert guard.count == 1
        guard.try_reserve("AAPL", "ORD-2")
        assert guard.count == 2
        guard.release("SPY")
        assert guard.count == 1

    def test_event_to_dict(self):
        guard = SingleTradeGuard()
        ev = guard.try_reserve("SPY", "ORD-1")
        d = ev.to_dict()
        required = {"event", "symbol", "order_id", "timestamp"}
        assert required.issubset(d.keys())
        assert d["event"] == "single_trade_reserved"

    def test_history_records_all(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        guard.try_reserve("SPY", "ORD-2")  # blocked
        guard.release("SPY")

        h = guard.history
        assert len(h) == 3
        assert h[0].event == "single_trade_reserved"
        assert h[1].event == "single_trade_blocked"
        assert h[2].event == "single_trade_released"

    def test_thread_safety_only_one_wins(self):
        guard = SingleTradeGuard()
        results = []
        barrier = threading.Barrier(10)

        def worker(i):
            barrier.wait()
            ev = guard.try_reserve("SPY", f"ORD-{i}")
            results.append(ev)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        reserved = [r for r in results if r.event == "single_trade_reserved"]
        blocked = [r for r in results if r.event == "single_trade_blocked"]
        assert len(reserved) == 1
        assert len(blocked) == 9

    def test_restore_reservations(self):
        guard = SingleTradeGuard()
        restored = guard.restore_reservations({"SPY": "ORD-1", "AAPL": "ORD-2"})
        assert restored == 2
        assert guard.is_reserved("SPY")
        assert guard.is_reserved("AAPL")

    def test_restore_skips_existing(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-OLD")
        restored = guard.restore_reservations({"SPY": "ORD-NEW"})
        assert restored == 0
        assert guard.get_reservation("SPY") == "ORD-OLD"

    def test_clear_all(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        guard.try_reserve("AAPL", "ORD-2")
        cleared = guard.clear_all()
        assert cleared == 2
        assert guard.count == 0

    def test_blocked_event_carries_blocking_id(self):
        guard = SingleTradeGuard()
        guard.try_reserve("SPY", "ORD-1")
        ev = guard.try_reserve("SPY", "ORD-2")
        assert ev.event == "single_trade_blocked"
        d = ev.to_dict()
        assert d["blocking_order_id"] == "ORD-1"
