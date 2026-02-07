"""
PATCH 9 — Make protective-stop lifecycle authoritative

INVARIANT:
    The StopLifecycleManager is the single source of truth for which
    protective stops are active.  Every lifecycle transition (place,
    cancel, restore) produces a journal-ready event.

TESTS:
    1.  place() stores stop and returns placed event.
    2.  cancel() removes stop and returns cancelled event.
    3.  get_stop_id() returns correct ID after place.
    4.  get_stop_id() returns None when no stop.
    5.  has_stop() true/false checks.
    6.  active_stops() returns snapshot.
    7.  Idempotent place: duplicate returns already_exists event.
    8.  Idempotent cancel: missing returns not_found event.
    9.  restore_from_events() replays placed + cancelled correctly.
   10.  restore_from_events() skips already-active stops.
   11.  Event to_dict() has required keys.
   12.  count property tracks active count.
   13.  history records all events in order.
   14.  Thread safety: concurrent place/cancel don't corrupt state.
   15.  Cancel after cancel is idempotent.
   16.  Place with stop_price includes it in event details.
"""

import threading
import pytest

from core.risk.protections.stop_lifecycle import (
    StopLifecycleEvent,
    StopLifecycleManager,
)


class TestStopLifecycleManager:

    def test_place_stores_stop(self):
        mgr = StopLifecycleManager()
        ev = mgr.place("SPY", stop_order_id="BRK-1", entry_order_id="ORD-1")

        assert ev.event == "protective_stop_placed"
        assert ev.symbol == "SPY"
        assert ev.stop_order_id == "BRK-1"
        assert mgr.get_stop_id("SPY") == "BRK-1"

    def test_cancel_removes_stop(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="BRK-1")
        ev = mgr.cancel("SPY")

        assert ev.event == "protective_stop_cancelled"
        assert ev.stop_order_id == "BRK-1"
        assert mgr.get_stop_id("SPY") is None

    def test_get_stop_id_returns_id(self):
        mgr = StopLifecycleManager()
        mgr.place("AAPL", stop_order_id="BRK-42")
        assert mgr.get_stop_id("AAPL") == "BRK-42"

    def test_get_stop_id_returns_none(self):
        mgr = StopLifecycleManager()
        assert mgr.get_stop_id("TSLA") is None

    def test_has_stop(self):
        mgr = StopLifecycleManager()
        assert mgr.has_stop("SPY") is False
        mgr.place("SPY", stop_order_id="BRK-1")
        assert mgr.has_stop("SPY") is True
        mgr.cancel("SPY")
        assert mgr.has_stop("SPY") is False

    def test_active_stops_snapshot(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="BRK-1")
        mgr.place("AAPL", stop_order_id="BRK-2")

        stops = mgr.active_stops()
        assert stops == {"SPY": "BRK-1", "AAPL": "BRK-2"}

    def test_idempotent_place(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="BRK-1")
        ev = mgr.place("SPY", stop_order_id="BRK-DUPE")

        assert ev.event == "protective_stop_already_exists"
        assert ev.stop_order_id == "BRK-1"  # original kept
        assert mgr.get_stop_id("SPY") == "BRK-1"

    def test_idempotent_cancel(self):
        mgr = StopLifecycleManager()
        ev = mgr.cancel("SPY")
        assert ev.event == "protective_stop_not_found"
        assert ev.stop_order_id is None

    def test_restore_from_events(self):
        mgr = StopLifecycleManager()
        events = [
            {"event": "protective_stop_placed", "symbol": "SPY", "stop_order_id": "BRK-1"},
            {"event": "protective_stop_placed", "symbol": "AAPL", "stop_order_id": "BRK-2"},
            {"event": "protective_stop_cancelled", "symbol": "SPY"},
        ]

        restored = mgr.restore_from_events(events)
        assert restored == 1  # only AAPL active after replay
        assert mgr.has_stop("AAPL") is True
        assert mgr.has_stop("SPY") is False

    def test_restore_skips_already_active(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="EXISTING")

        events = [
            {"event": "protective_stop_placed", "symbol": "SPY", "stop_order_id": "BRK-NEW"},
        ]

        restored = mgr.restore_from_events(events)
        assert restored == 0  # already present
        assert mgr.get_stop_id("SPY") == "EXISTING"

    def test_event_to_dict_has_required_keys(self):
        mgr = StopLifecycleManager()
        ev = mgr.place("SPY", stop_order_id="BRK-1", entry_order_id="ORD-1")
        d = ev.to_dict()

        required = {"event", "symbol", "stop_order_id", "entry_order_id", "timestamp"}
        assert required.issubset(d.keys())
        assert d["event"] == "protective_stop_placed"

    def test_count_property(self):
        mgr = StopLifecycleManager()
        assert mgr.count == 0
        mgr.place("SPY", stop_order_id="BRK-1")
        assert mgr.count == 1
        mgr.place("AAPL", stop_order_id="BRK-2")
        assert mgr.count == 2
        mgr.cancel("SPY")
        assert mgr.count == 1

    def test_history_records_all_events(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="BRK-1")
        mgr.cancel("SPY")
        mgr.cancel("SPY")  # idempotent cancel

        history = mgr.history
        assert len(history) == 3
        assert history[0].event == "protective_stop_placed"
        assert history[1].event == "protective_stop_cancelled"
        assert history[2].event == "protective_stop_not_found"

    def test_thread_safety(self):
        mgr = StopLifecycleManager()
        barrier = threading.Barrier(10)
        errors = []

        def worker(i):
            try:
                barrier.wait()
                sym = f"SYM-{i}"
                mgr.place(sym, stop_order_id=f"BRK-{i}")
                assert mgr.has_stop(sym)
                mgr.cancel(sym)
                assert not mgr.has_stop(sym)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert mgr.count == 0

    def test_cancel_after_cancel(self):
        mgr = StopLifecycleManager()
        mgr.place("SPY", stop_order_id="BRK-1")
        ev1 = mgr.cancel("SPY")
        ev2 = mgr.cancel("SPY")

        assert ev1.event == "protective_stop_cancelled"
        assert ev2.event == "protective_stop_not_found"

    def test_place_with_stop_price(self):
        mgr = StopLifecycleManager()
        ev = mgr.place("SPY", stop_order_id="BRK-1", stop_price="95.50")

        assert ev.event == "protective_stop_placed"
        d = ev.to_dict()
        assert d.get("stop_price") == "95.50"
