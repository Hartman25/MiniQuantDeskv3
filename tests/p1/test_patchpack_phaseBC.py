"""
Tests for Phase B + Phase C patch pack (Patches 3-10).

PATCH 3: Recovery restore path must be truthful (no placeholder restore)
PATCH 4: Recovery status propagation + runtime halt enforcement
PATCH 5: Snapshots must be health-aware in LIVE (no infinite soft-fail)
PATCH 6: UserStreamTracker correctness (option A: disabled in Phase 1)
PATCH 7: OrderTracker thread-safety guard
PATCH 8: Event bus health surfacing (dropped counter)
PATCH 9: Duplicate subsystem import guard
PATCH 10: Explicit authoritative state contract
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, List, Optional

import pytest


# ============================================================================
# PATCH 3: Recovery restore path must be truthful
# ============================================================================

class TestPatch3TruthfulRestore:
    """Restore path does not claim orders are restored into tracker if not."""

    def test_restore_order_does_not_claim_restored(self):
        """_restore_order must NOT log 'Restored order' — it should log 'skipped'."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence, OrderSnapshot

        persistence = MagicMock(spec=StatePersistence)
        broker = MagicMock()
        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True,
        )

        snap = OrderSnapshot(
            order_id="ORD-001",
            broker_order_id="BROKER-001",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("10"),
            order_type="STOP",
            limit_price=None,
            status="SUBMITTED",
            submitted_at=datetime.now(timezone.utc),
        )

        # Call _restore_order — it must not raise, and must log "skipped"
        with patch.object(coord, "logger") as mock_logger:
            coord._restore_order(snap)
            # Check the info call contains "skipped" or "Skipped"
            assert mock_logger.info.called
            log_msg = mock_logger.info.call_args[0][0]
            assert "skip" in log_msg.lower() or "Skipped" in log_msg

    def test_paper_recovery_cancel_rebuild_no_tracker_insert(self):
        """Paper mode: recovery uses cancel+rebuild, order tracker not populated."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import (
            StatePersistence,
            SystemStateSnapshot,
            OrderSnapshot,
            PositionSnapshot,
        )

        persistence = MagicMock(spec=StatePersistence)
        broker = MagicMock()
        broker.get_orders.return_value = []
        broker.get_positions.return_value = []

        position_store = MagicMock()
        order_machine = MagicMock()

        # Saved state has a pending order
        saved_state = SystemStateSnapshot(
            timestamp=datetime.now(timezone.utc),
            positions=[],
            pending_orders=[
                OrderSnapshot(
                    order_id="ORD-001",
                    broker_order_id="BROKER-001",
                    symbol="SPY",
                    side="BUY",
                    quantity=Decimal("10"),
                    order_type="STOP",
                    limit_price=None,
                    status="SUBMITTED",
                    submitted_at=datetime.now(timezone.utc),
                )
            ],
        )
        persistence.load_latest_state.return_value = saved_state

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True,
        )

        report = coord.recover()
        # The order should be counted as cancelled (not restored to broker/tracker)
        assert report.orders_cancelled == 1
        assert report.orders_recovered == 0


class TestPatch3LiveFailClosed:
    """Live mode: if cancel fails, recovery returns FAILED."""

    def test_live_cancel_failure_returns_failed(self):
        """If any cancel fails in live mode, recovery raises → FAILED."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None  # no saved state → rebuild from broker

        broker = MagicMock()
        # get_orders returns working orders
        mock_order = MagicMock()
        mock_order.id = "ORD-001"
        mock_order.symbol = "SPY"
        mock_order.status = "new"
        broker.get_orders.return_value = [mock_order]
        # Cancel fails
        broker.cancel_order.side_effect = RuntimeError("cancel failed")

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=False,  # LIVE
        )

        report = coord.recover()
        assert report.status == RecoveryStatus.FAILED


# ============================================================================
# PATCH 4: Recovery status propagation + runtime halt enforcement
# ============================================================================

class TestPatch4RecoveryHaltEnforcement:
    """Runtime must not start trading loop if recovery FAILED in live mode."""

    def test_try_recovery_live_exception_returns_failed(self):
        """In live mode, _try_recovery exception returns FAILED, not REBUILT."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        broker = MagicMock()
        position_store = MagicMock()
        order_machine = MagicMock()

        # Force recovery to raise by making StatePersistence constructor fail
        with patch(
            "core.runtime.app.StatePersistence",
            side_effect=RuntimeError("disk error"),
        ):
            status = _try_recovery(
                broker, position_store, order_machine, paper_mode=False
            )
        assert status == RecoveryStatus.FAILED

    def test_try_recovery_paper_exception_returns_rebuilt(self):
        """In paper mode, _try_recovery exception returns REBUILT (fail-open)."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        broker = MagicMock()
        position_store = MagicMock()
        order_machine = MagicMock()

        with patch(
            "core.runtime.app.StatePersistence",
            side_effect=RuntimeError("disk error"),
        ):
            status = _try_recovery(
                broker, position_store, order_machine, paper_mode=True
            )
        assert status == RecoveryStatus.REBUILT


# ============================================================================
# PATCH 5: Snapshot health monitor
# ============================================================================

class TestPatch5SnapshotHealthMonitor:
    """Snapshot failures must eventually trigger halt in live mode."""

    def test_consecutive_failures_trigger_is_failed(self):
        """After N consecutive failures, is_failed becomes True."""
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        assert not mon.is_failed

        mon.record_failure()
        assert not mon.is_failed
        mon.record_failure()
        assert not mon.is_failed
        mon.record_failure()
        assert mon.is_failed

    def test_success_resets_counter(self):
        """A single success resets the consecutive failure counter."""
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        mon.record_failure()
        mon.record_failure()
        mon.record_success()  # resets
        mon.record_failure()
        assert not mon.is_failed  # only 1 consecutive failure

    def test_paper_mode_does_not_halt(self):
        """Paper mode: monitor still reports is_failed but caller decides.

        We test that the monitor object works identically regardless of
        mode — the mode-based decision is at the call site, not in the
        monitor itself.
        """
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=2)
        mon.record_failure()
        mon.record_failure()
        assert mon.is_failed  # monitor reports failure
        # Paper caller would log + continue (tested at integration level)

    def test_get_stats(self):
        """get_stats returns expected fields."""
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        mon.record_failure()
        mon.record_success()
        mon.record_failure()

        stats = mon.get_stats()
        assert stats["consecutive_failures"] == 1
        assert stats["total_successes"] == 1
        assert stats["total_failures"] == 2
        assert stats["threshold"] == 3
        assert stats["is_failed"] is False


# ============================================================================
# PATCH 6: UserStreamTracker — Option A: disabled in Phase 1
# ============================================================================

class TestPatch6UserStreamDisabledPhase1:
    """UserStreamTracker must not auto-start in Phase 1 runtime.

    Option A chosen: Phase 1 baseline does not run realtime user stream.
    The Container creates it, but container.start() does NOT call
    user_stream.start() — that requires start_async() which is not
    invoked in the synchronous runtime loop (app.py).
    """

    def test_container_start_does_not_start_user_stream(self):
        """container.start() must NOT start the user stream (sync only)."""
        from core.di.container import Container

        container = Container.__new__(Container)
        container._event_bus = MagicMock()
        container._user_stream = MagicMock()

        container.start()

        # event bus starts
        container._event_bus.start.assert_called_once()
        # user stream does NOT start (start_async is separate)
        container._user_stream.start.assert_not_called()

    def test_user_stream_not_started_in_sync_run(self):
        """The sync runtime (app.py run()) never calls start_async."""
        # This is a static analysis test — start_async is not called in run()
        import inspect
        from core.runtime import app as app_module

        source = inspect.getsource(app_module.run)
        assert "start_async" not in source, (
            "run() must not call start_async — Phase 1 is sync-only"
        )


# ============================================================================
# PATCH 7: OrderTracker thread-safety guard
# ============================================================================

class TestPatch7OrderTrackerThreadSafety:
    """OrderTracker mutation methods must be thread-safe."""

    def test_has_lock(self):
        """OrderTracker must have a threading.Lock."""
        from core.state.order_tracker import OrderTracker

        tracker = OrderTracker()
        assert hasattr(tracker, "_lock")
        assert isinstance(tracker._lock, type(threading.Lock()))

    def test_concurrent_process_fill_no_exception(self):
        """Concurrent fills must not raise or corrupt state."""
        from core.state.order_tracker import (
            OrderTracker,
            InFlightOrder,
            FillEvent,
            OrderSide,
        )

        tracker = OrderTracker()
        order = InFlightOrder(
            client_order_id="ORD-001",
            symbol="SPY",
            quantity=Decimal("100"),
            side=OrderSide.BUY,
        )
        tracker.start_tracking(order)

        errors = []

        def fill_worker(i):
            try:
                fill = FillEvent(
                    timestamp=datetime.now(timezone.utc),
                    quantity=Decimal("1"),
                    price=Decimal("100.00"),
                )
                tracker.process_fill("ORD-001", fill)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fill_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent fills raised: {errors}"

    def test_concurrent_start_stop_tracking(self):
        """Concurrent start_tracking and stop_tracking must not raise."""
        from core.state.order_tracker import (
            OrderTracker,
            InFlightOrder,
            OrderSide,
        )

        tracker = OrderTracker()
        errors = []

        def worker(i):
            try:
                oid = f"ORD-{i:04d}"
                order = InFlightOrder(
                    client_order_id=oid,
                    symbol="AAPL",
                    quantity=Decimal("10"),
                    side=OrderSide.BUY,
                )
                tracker.start_tracking(order)
                tracker.stop_tracking(oid, "completed")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent start/stop raised: {errors}"

    def test_concurrent_process_order_update(self):
        """Concurrent process_order_update must not raise."""
        from core.state.order_tracker import (
            OrderTracker,
            InFlightOrder,
            OrderSide,
        )

        tracker = OrderTracker()
        order = InFlightOrder(
            client_order_id="ORD-001",
            symbol="SPY",
            quantity=Decimal("10"),
            side=OrderSide.BUY,
        )
        tracker.start_tracking(order)

        errors = []

        def update_worker(i):
            try:
                tracker.process_order_update("ORD-001", {
                    "exchange_order_id": f"EX-{i}",
                })
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent updates raised: {errors}"


# ============================================================================
# PATCH 8: Event bus health surfacing
# ============================================================================

class TestPatch8EventBusHealthSurfacing:
    """Event bus must surface dropped events in stats."""

    def test_get_stats_includes_dropped_count(self):
        """get_stats must include events_dropped."""
        from core.events.bus import OrderEventBus

        bus = OrderEventBus(max_queue_size=10)
        stats = bus.get_stats()
        assert "events_dropped" in stats

    def test_dropped_counter_increments_on_full_queue(self):
        """When queue is full, emit does not raise but increments dropped."""
        from core.events.bus import OrderEventBus, Event

        bus = OrderEventBus(max_queue_size=2)
        bus._running = True  # enable emit without starting thread

        class DummyEvent(Event):
            def to_dict(self):
                return {}

        evt = DummyEvent(timestamp=datetime.now(timezone.utc))

        # Fill queue
        bus._queue.put(evt)
        bus._queue.put(evt)

        # Next emit should drop
        bus.emit(evt)  # must not raise

        stats = bus.get_stats()
        assert stats["events_dropped"] >= 1

    def test_emit_does_not_raise_on_full_queue(self):
        """emit() must be non-fatal even when queue is full."""
        from core.events.bus import OrderEventBus, Event

        bus = OrderEventBus(max_queue_size=1)
        bus._running = True

        class DummyEvent(Event):
            def to_dict(self):
                return {}

        evt = DummyEvent(timestamp=datetime.now(timezone.utc))
        bus._queue.put(evt)

        # Should NOT raise
        bus.emit(evt)


# ============================================================================
# PATCH 9: Duplicate subsystem import guard
# ============================================================================

class TestPatch9DeprecatedImportGuard:
    """Runtime entrypoints must not import from deprecated module paths."""

    ENTRYPOINT_FILES = [
        "core/runtime/app.py",
        "entry_paper.py",
        "entry_live.py",
        "core/di/container.py",
    ]

    DEPRECATED_PATTERNS = [
        "core.risk_management",
        "core.realtime.realtime",
    ]

    def _get_repo_root(self) -> Path:
        """Walk up from this test file to find repo root."""
        p = Path(__file__).resolve()
        while p.parent != p:
            if (p / "core").is_dir() and (p / "entry_paper.py").is_file():
                return p
            p = p.parent
        raise RuntimeError("Cannot find repo root")

    @pytest.mark.parametrize("rel_path", ENTRYPOINT_FILES)
    def test_no_deprecated_imports(self, rel_path):
        """Entrypoint file must not import from deprecated module paths."""
        root = self._get_repo_root()
        full_path = root / rel_path
        if not full_path.exists():
            pytest.skip(f"{rel_path} not found")

        source = full_path.read_text(encoding="utf-8")
        for pattern in self.DEPRECATED_PATTERNS:
            assert pattern not in source, (
                f"{rel_path} imports from deprecated path '{pattern}'. "
                f"Use the canonical module instead."
            )


# ============================================================================
# PATCH 10: Explicit authoritative state contract
# ============================================================================

class TestPatch10AuthoritativeStateContract:
    """Recovery must follow broker > snapshot > local priority."""

    def test_coordinator_docstring_documents_priority(self):
        """RecoveryCoordinator docstring must document truth priority."""
        from core.recovery.coordinator import RecoveryCoordinator

        doc = RecoveryCoordinator.__doc__ or ""
        # Must mention all three levels of truth
        assert "broker" in doc.lower(), "Docstring must mention broker truth"
        assert "snapshot" in doc.lower(), "Docstring must mention snapshot"

    def test_broker_reachable_uses_broker_as_truth(self):
        """When broker is reachable, uses broker positions as truth."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None  # no snapshot

        broker = MagicMock()
        mock_pos = MagicMock()
        mock_pos.symbol = "SPY"
        mock_pos.qty = "10"
        mock_pos.avg_entry_price = "450.00"
        broker.get_positions.return_value = [mock_pos]
        broker.get_orders.return_value = []

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True,
        )

        report = coord.recover()
        assert report.status == RecoveryStatus.REBUILT
        assert report.positions_rebuilt == 1
        # position_store.restore_position should have been called with broker data
        position_store.restore_position.assert_called_once()
        call_kwargs = position_store.restore_position.call_args
        assert call_kwargs[1]["symbol"] == "SPY" or call_kwargs.kwargs.get("symbol") == "SPY"

    def test_broker_unreachable_live_fails_closed(self):
        """Live mode + broker unreachable → recovery FAILED."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None  # no snapshot

        broker = MagicMock()
        broker.get_orders.side_effect = ConnectionError("broker down")
        broker.get_positions.side_effect = ConnectionError("broker down")

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=False,
        )

        report = coord.recover()
        # In live mode with no saved state and broker down, cancel_open_orders
        # will fail because get_orders raises. The outer try/except catches
        # and returns FAILED.
        assert report.status == RecoveryStatus.FAILED

    def test_broker_unreachable_paper_continues(self):
        """Paper mode + broker unreachable → recovery continues (fail-open)."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None

        broker = MagicMock()
        broker.get_orders.side_effect = ConnectionError("broker down")
        broker.get_positions.return_value = []  # positions returns empty

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True,
        )

        report = coord.recover()
        # Paper mode absorbs cancel failures and continues
        assert report.status in (RecoveryStatus.REBUILT, RecoveryStatus.PARTIAL)
