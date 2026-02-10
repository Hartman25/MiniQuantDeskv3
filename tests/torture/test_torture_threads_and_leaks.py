"""
Torture test: Thread leak detection.

Verifies:
  - No accumulation of OrderEventBus threads after multiple start/stop cycles.
  - Event bus start/stop is clean.
  - Thread count returns to baseline after bus lifecycle.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.events.bus import OrderEventBus, Event, OrderStateChangedEvent


def _count_eventbus_threads() -> int:
    """Count threads whose name contains 'OrderEventBus'."""
    return sum(
        1 for t in threading.enumerate()
        if "OrderEventBus" in t.name and t.is_alive()
    )


def _all_thread_names() -> list[str]:
    """Snapshot of all live thread names."""
    return [t.name for t in threading.enumerate() if t.is_alive()]


class TestOrderEventBusLeaks:
    """Verify no thread accumulation across OrderEventBus lifecycles."""

    def test_single_start_stop_no_leak(self):
        """One start/stop cycle leaves no orphan threads."""
        baseline = _count_eventbus_threads()

        bus = OrderEventBus(daemon=True)
        bus.start()
        assert _count_eventbus_threads() == baseline + 1

        bus.stop(timeout=2.0)
        # Give thread a moment to fully terminate
        time.sleep(0.1)
        assert _count_eventbus_threads() == baseline

    def test_three_lifecycles_no_accumulation(self):
        """Three start/stop cycles should not accumulate threads."""
        baseline = _count_eventbus_threads()

        for i in range(3):
            bus = OrderEventBus(daemon=True)
            bus.start()
            assert _count_eventbus_threads() == baseline + 1, (
                f"Cycle {i}: expected {baseline + 1} bus threads, "
                f"got {_count_eventbus_threads()}"
            )
            bus.stop(timeout=2.0)
            time.sleep(0.1)

        final = _count_eventbus_threads()
        assert final == baseline, (
            f"Thread leak: started with {baseline}, ended with {final}. "
            f"Threads: {_all_thread_names()}"
        )

    def test_five_lifecycles_thread_names_stable(self):
        """After 5 start/stop cycles, thread names don't show accumulated buses."""
        baseline_names = set(_all_thread_names())

        for _ in range(5):
            bus = OrderEventBus(daemon=True)
            bus.start()
            bus.stop(timeout=2.0)
            time.sleep(0.05)

        final_names = set(_all_thread_names())
        # Filter for bus-related threads
        baseline_bus = {n for n in baseline_names if "OrderEventBus" in n}
        final_bus = {n for n in final_names if "OrderEventBus" in n}

        leaked = final_bus - baseline_bus
        assert len(leaked) == 0, f"Leaked bus threads: {leaked}"

    def test_bus_with_events_no_leak(self):
        """Bus that processes events still cleans up."""
        from datetime import datetime, timezone

        baseline = _count_eventbus_threads()

        for _ in range(3):
            bus = OrderEventBus(daemon=True)

            events_seen = []

            def handler(event):
                events_seen.append(event)

            bus.subscribe(OrderStateChangedEvent, handler)
            bus.start()

            # Emit a few events
            for j in range(5):
                from core.state import OrderStatus
                bus.emit(OrderStateChangedEvent(
                    timestamp=datetime.now(timezone.utc),
                    order_id=f"test-{j}",
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                ))

            # Let events process
            time.sleep(0.2)
            bus.stop(timeout=2.0)
            time.sleep(0.1)

        final = _count_eventbus_threads()
        assert final == baseline, (
            f"Thread leak after event processing: {baseline} → {final}"
        )


class TestContextManagerCleanup:
    """Event bus context manager should clean up threads."""

    def test_context_manager_no_leak(self):
        """Using bus as context manager cleans up on exit."""
        baseline = _count_eventbus_threads()

        for _ in range(3):
            with OrderEventBus(daemon=True) as bus:
                assert _count_eventbus_threads() == baseline + 1
            time.sleep(0.1)

        final = _count_eventbus_threads()
        assert final == baseline, f"Context manager leak: {baseline} → {final}"


class TestBaselineThreadSafety:
    """General thread safety checks."""

    def test_no_new_bus_threads_from_lifecycle(self):
        """A start/stop cycle should not leave new threads beyond baseline."""
        baseline = _count_eventbus_threads()

        bus = OrderEventBus(daemon=True)
        bus.start()
        bus.stop(timeout=2.0)
        time.sleep(0.1)

        final = _count_eventbus_threads()
        assert final <= baseline, (
            f"Bus lifecycle leaked threads: {baseline} -> {final}. "
            f"Threads: {_all_thread_names()}"
        )

    def test_double_stop_does_not_crash(self):
        """Calling stop() on a stopped bus raises RuntimeError (expected)."""
        bus = OrderEventBus(daemon=True)
        bus.start()
        bus.stop(timeout=2.0)

        with pytest.raises(RuntimeError, match="not running"):
            bus.stop(timeout=1.0)
