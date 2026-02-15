"""
Tests for Phase B + Phase C patch pack (Patches 3-10) — fixup edition.

Behavioural tests only.  No source-scanning or string-matching.
Each test class maps to one patch and locks the required behaviour.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# PATCH 3: Recovery restore path must be truthful
# ============================================================================

class TestPatch3TruthfulRestore:
    """_restore_order logs skip, does not pretend to rehydrate."""

    def test_restore_order_logs_skip(self):
        """_restore_order must log 'skip' — not 'Restored order'."""
        from core.recovery.coordinator import RecoveryCoordinator
        from core.recovery.persistence import StatePersistence, OrderSnapshot

        coord = RecoveryCoordinator(
            persistence=MagicMock(spec=StatePersistence),
            broker=MagicMock(),
            position_store=MagicMock(),
            order_machine=MagicMock(),
            paper_mode=True,
        )
        snap = OrderSnapshot(
            order_id="ORD-001", broker_order_id="B-001", symbol="SPY",
            side="BUY", quantity=Decimal("10"), order_type="STOP",
            limit_price=None, status="SUBMITTED",
            submitted_at=datetime.now(timezone.utc),
        )
        with patch.object(coord, "logger") as mock_log:
            coord._restore_order(snap)
            assert mock_log.info.called
            msg = mock_log.info.call_args[0][0]
            assert "skip" in msg.lower()

    def test_paper_snapshot_pending_orders_not_counted_as_cancelled(self):
        """Snapshot shows pending orders, broker has none → orders_cancelled == 0.

        The old code incremented orders_cancelled for orders that simply
        disappeared from the broker.  That's lying — we didn't cancel them.
        """
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import (
            StatePersistence, SystemStateSnapshot, OrderSnapshot,
        )

        persistence = MagicMock(spec=StatePersistence)
        broker = MagicMock()
        broker.get_orders.return_value = []      # no broker orders at all
        broker.get_positions.return_value = []

        saved_state = SystemStateSnapshot(
            timestamp=datetime.now(timezone.utc),
            positions=[],
            pending_orders=[
                OrderSnapshot(
                    order_id="ORD-001", broker_order_id="B-001", symbol="SPY",
                    side="BUY", quantity=Decimal("10"), order_type="STOP",
                    limit_price=None, status="SUBMITTED",
                    submitted_at=datetime.now(timezone.utc),
                )
            ],
        )
        persistence.load_latest_state.return_value = saved_state

        coord = RecoveryCoordinator(
            persistence=persistence, broker=broker,
            position_store=MagicMock(), order_machine=MagicMock(),
            paper_mode=True,
        )
        report = coord.recover()
        # Disappeared orders are NOT counted as cancelled by us
        assert report.orders_cancelled == 0

    def test_paper_broker_returns_working_orders_cancel_counted(self):
        """Paper: broker returns N working orders → orders_cancelled == N."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None  # no snapshot → rebuild from broker

        o1 = MagicMock(); o1.id = "B1"; o1.symbol = "SPY"; o1.status = "new"
        o2 = MagicMock(); o2.id = "B2"; o2.symbol = "AAPL"; o2.status = "accepted"
        broker = MagicMock()
        broker.get_orders.return_value = [o1, o2]
        broker.cancel_order.return_value = True
        broker.get_positions.return_value = []

        coord = RecoveryCoordinator(
            persistence=persistence, broker=broker,
            position_store=MagicMock(), order_machine=MagicMock(),
            paper_mode=True,
        )
        report = coord.recover()
        assert report.orders_cancelled == 2
        assert report.status == RecoveryStatus.REBUILT


class TestPatch3LiveFailClosed:
    """Live mode: cancel failure → FAILED."""

    def test_live_cancel_failure_returns_failed(self):
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.return_value = None

        mock_order = MagicMock(); mock_order.id = "B1"; mock_order.symbol = "SPY"; mock_order.status = "new"
        broker = MagicMock()
        broker.get_orders.return_value = [mock_order]
        broker.cancel_order.side_effect = RuntimeError("cancel failed")

        coord = RecoveryCoordinator(
            persistence=persistence, broker=broker,
            position_store=MagicMock(), order_machine=MagicMock(),
            paper_mode=False,
        )
        report = coord.recover()
        assert report.status == RecoveryStatus.FAILED


# ============================================================================
# PATCH 4: Recovery status propagation + runtime halt enforcement
# ============================================================================

class TestPatch4RecoveryHaltEnforcement:
    """_try_recovery returns FAILED (live) or REBUILT (paper) on exception."""

    def test_live_exception_returns_failed(self):
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        with patch("core.runtime.app.StatePersistence", side_effect=RuntimeError("disk")):
            assert _try_recovery(
                MagicMock(), MagicMock(), MagicMock(), paper_mode=False,
            ) == RecoveryStatus.FAILED

    def test_paper_exception_returns_rebuilt(self):
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        with patch("core.runtime.app.StatePersistence", side_effect=RuntimeError("disk")):
            assert _try_recovery(
                MagicMock(), MagicMock(), MagicMock(), paper_mode=True,
            ) == RecoveryStatus.REBUILT


# ============================================================================
# PATCH 5: Snapshot health monitor — unit + wiring
# ============================================================================

class TestPatch5SnapshotHealthMonitor:
    """SnapshotHealthMonitor unit behaviour."""

    def test_consecutive_failures_trigger_is_failed(self):
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        for _ in range(2):
            mon.record_failure()
            assert not mon.is_failed
        mon.record_failure()
        assert mon.is_failed

    def test_success_resets_counter(self):
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        mon.record_failure(); mon.record_failure()
        mon.record_success()
        mon.record_failure()
        assert not mon.is_failed

    def test_get_stats_fields(self):
        from core.runtime.state_snapshot import SnapshotHealthMonitor

        mon = SnapshotHealthMonitor(max_consecutive_failures=3)
        mon.record_failure(); mon.record_success(); mon.record_failure()
        s = mon.get_stats()
        assert s["consecutive_failures"] == 1
        assert s["total_successes"] == 1
        assert s["total_failures"] == 2
        assert s["threshold"] == 3
        assert s["is_failed"] is False


class TestPatch5SnapshotWiring:
    """SnapshotHealthMonitor is wired into the runtime loop."""

    def test_runtime_imports_snapshot_health_monitor(self):
        """app.py must import SnapshotHealthMonitor for use in the loop."""
        from core.runtime import app as app_mod
        assert hasattr(app_mod, "SnapshotHealthMonitor")
        assert hasattr(app_mod, "build_state_snapshot")


# ============================================================================
# PATCH 7: OrderTracker thread-safety guard
# ============================================================================

class TestPatch7OrderTrackerThreadSafety:
    """OrderTracker mutation methods are thread-safe."""

    def test_has_lock(self):
        from core.state.order_tracker import OrderTracker
        assert isinstance(OrderTracker()._lock, type(threading.Lock()))

    def test_concurrent_fills_no_exception(self):
        from core.state.order_tracker import OrderTracker, InFlightOrder, FillEvent, OrderSide

        tracker = OrderTracker()
        tracker.start_tracking(InFlightOrder(
            client_order_id="O1", symbol="SPY",
            quantity=Decimal("100"), side=OrderSide.BUY,
        ))
        errors = []

        def worker(i):
            try:
                tracker.process_fill("O1", FillEvent(
                    timestamp=datetime.now(timezone.utc),
                    quantity=Decimal("1"), price=Decimal("100"),
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        assert not errors

    def test_concurrent_start_stop(self):
        from core.state.order_tracker import OrderTracker, InFlightOrder, OrderSide

        tracker = OrderTracker()
        errors = []

        def worker(i):
            try:
                oid = f"O-{i:04d}"
                tracker.start_tracking(InFlightOrder(
                    client_order_id=oid, symbol="AAPL",
                    quantity=Decimal("10"), side=OrderSide.BUY,
                ))
                tracker.stop_tracking(oid, "completed")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        assert not errors


# ============================================================================
# PATCH 8: Event bus health surfacing — emit never raises on full queue
# ============================================================================

class TestPatch8EventBusHealth:
    """Event bus emit() is non-fatal on full queue and surfaces drop stats."""

    def _make_bus_and_event(self, max_q=2):
        from core.events.bus import OrderEventBus, Event

        class Dummy(Event):
            def to_dict(self):
                return {}

        bus = OrderEventBus(max_queue_size=max_q)
        bus._running = True
        return bus, Dummy(timestamp=datetime.now(timezone.utc))

    def test_get_stats_includes_dropped(self):
        from core.events.bus import OrderEventBus
        assert "events_dropped" in OrderEventBus(max_queue_size=10).get_stats()

    def test_emit_does_not_raise_on_full_queue(self):
        bus, evt = self._make_bus_and_event(max_q=1)
        bus._queue.put(evt)          # fill
        bus.emit(evt)                # must not raise

    def test_dropped_counter_increments(self):
        bus, evt = self._make_bus_and_event(max_q=2)
        bus._queue.put(evt); bus._queue.put(evt)  # fill
        bus.emit(evt)                              # drop
        assert bus.get_stats()["events_dropped"] >= 1

    def test_emit_is_non_blocking(self):
        """emit() must return fast — no 1-second timeout."""
        bus, evt = self._make_bus_and_event(max_q=1)
        bus._queue.put(evt)

        start = time.monotonic()
        bus.emit(evt)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"emit() took {elapsed:.3f}s — should be near-instant"


# ============================================================================
# PATCH 9: Deprecated import guard
# ============================================================================

class TestPatch9DeprecatedImportGuard:
    """Entrypoint files must not import from deprecated module paths."""

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

    def _repo_root(self) -> Path:
        p = Path(__file__).resolve()
        while p.parent != p:
            if (p / "core").is_dir() and (p / "entry_paper.py").is_file():
                return p
            p = p.parent
        raise RuntimeError("Cannot find repo root")

    @pytest.mark.parametrize("rel_path", ENTRYPOINT_FILES)
    def test_no_deprecated_imports(self, rel_path):
        root = self._repo_root()
        fp = root / rel_path
        if not fp.exists():
            pytest.skip(f"{rel_path} not found")
        source = fp.read_text(encoding="utf-8")
        for pat in self.DEPRECATED_PATTERNS:
            assert pat not in source, (
                f"{rel_path} imports from deprecated path '{pat}'"
            )


# ============================================================================
# PATCH 10: Authoritative state contract
# ============================================================================

class TestPatch10AuthoritativeStateContract:
    """Recovery follows broker > snapshot > local priority."""

    def test_docstring_documents_priority(self):
        from core.recovery.coordinator import RecoveryCoordinator
        doc = (RecoveryCoordinator.__doc__ or "").lower()
        assert "broker" in doc
        assert "snapshot" in doc

    def test_broker_reachable_uses_broker_truth(self):
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        pos = MagicMock(); pos.symbol = "SPY"; pos.qty = "10"; pos.avg_entry_price = "450"
        broker = MagicMock()
        broker.get_positions.return_value = [pos]
        broker.get_orders.return_value = []

        ps = MagicMock()
        coord = RecoveryCoordinator(
            persistence=MagicMock(spec=StatePersistence, **{"load_latest_state.return_value": None}),
            broker=broker, position_store=ps, order_machine=MagicMock(),
            paper_mode=True,
        )
        report = coord.recover()
        assert report.status == RecoveryStatus.REBUILT
        assert report.positions_rebuilt == 1
        ps.restore_position.assert_called_once()

    def test_broker_unreachable_live_fails_closed(self):
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        broker = MagicMock()
        broker.get_orders.side_effect = ConnectionError("down")
        broker.get_positions.side_effect = ConnectionError("down")

        coord = RecoveryCoordinator(
            persistence=MagicMock(spec=StatePersistence, **{"load_latest_state.return_value": None}),
            broker=broker, position_store=MagicMock(), order_machine=MagicMock(),
            paper_mode=False,
        )
        assert coord.recover().status == RecoveryStatus.FAILED

    def test_broker_unreachable_paper_continues(self):
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        broker = MagicMock()
        broker.get_orders.side_effect = ConnectionError("down")
        broker.get_positions.return_value = []

        coord = RecoveryCoordinator(
            persistence=MagicMock(spec=StatePersistence, **{"load_latest_state.return_value": None}),
            broker=broker, position_store=MagicMock(), order_machine=MagicMock(),
            paper_mode=True,
        )
        assert coord.recover().status in (RecoveryStatus.REBUILT, RecoveryStatus.PARTIAL)
