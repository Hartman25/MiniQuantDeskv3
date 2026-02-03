"""
P1-O3 — Order Latency Logging

INVARIANT:
    The system MUST record timestamps for every order lifecycle event
    and provide a mechanism to compute fill latency (submit → fill).

TESTS:
    1. Order.submitted_at is set on PENDING→SUBMITTED transition
    2. Order.filled_at is set on SUBMITTED→FILLED transition
    3. ExecutionMetric.calculate_metrics computes fill_time_seconds
    4. ExecutionMetric.calculate_metrics computes slippage_bps
    5. is_order_stale returns True when order exceeds TTL
    6. is_order_stale returns False when order is within TTL
    7. is_order_stale returns False when order has no metadata
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.state.order_machine import OrderStateMachine, OrderStatus
from core.monitoring.execution import ExecutionMetric


# ---------------------------------------------------------------------------
# 1-2. Order timestamp fields
# ---------------------------------------------------------------------------

class TestOrderTimestamps:

    @pytest.fixture
    def order_machine(self, tmp_path):
        from core.events.bus import OrderEventBus
        from core.state.transaction_log import TransactionLog

        bus = OrderEventBus()
        bus.start()
        txlog = TransactionLog(path=tmp_path / "o3_txn.ndjson")
        sm = OrderStateMachine(event_bus=bus, transaction_log=txlog)
        yield sm
        txlog.close()
        bus.stop()

    def test_submitted_at_set_on_transition(self, order_machine):
        """submitted_at is set when order transitions to SUBMITTED."""
        order_machine.create_order(
            order_id="LAT-001", symbol="SPY", quantity=Decimal("10"),
            side="LONG", order_type="MARKET", strategy="T",
        )
        order_machine.transition(
            order_id="LAT-001",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK-1",
        )

        order = order_machine.get_order("LAT-001")
        assert order.submitted_at is not None, "submitted_at must be set on SUBMITTED transition"
        assert isinstance(order.submitted_at, datetime)

    def test_filled_at_set_on_transition(self, order_machine):
        """filled_at is set when order transitions to FILLED."""
        order_machine.create_order(
            order_id="LAT-002", symbol="SPY", quantity=Decimal("10"),
            side="LONG", order_type="MARKET", strategy="T",
        )
        order_machine.transition(
            order_id="LAT-002",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK-2",
        )
        order_machine.transition(
            order_id="LAT-002",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK-2",
            filled_qty=Decimal("10"),
            fill_price=Decimal("450.00"),
        )

        order = order_machine.get_order("LAT-002")
        assert order.filled_at is not None, "filled_at must be set on FILLED transition"
        assert isinstance(order.filled_at, datetime)
        # filled_at must be >= submitted_at
        assert order.filled_at >= order.submitted_at

    def test_cancelled_at_set_on_transition(self, order_machine):
        """cancelled_at is set when order transitions to CANCELLED."""
        order_machine.create_order(
            order_id="LAT-003", symbol="SPY", quantity=Decimal("10"),
            side="LONG", order_type="MARKET", strategy="T",
        )
        order_machine.transition(
            order_id="LAT-003",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK-3",
        )
        order_machine.transition(
            order_id="LAT-003",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.CANCELLED,
            broker_order_id="BRK-3",
            reason="timeout",
        )

        order = order_machine.get_order("LAT-003")
        assert order.cancelled_at is not None, "cancelled_at must be set on CANCELLED transition"


# ---------------------------------------------------------------------------
# 3-4. ExecutionMetric latency calculation
# ---------------------------------------------------------------------------

class TestExecutionMetricLatency:

    def test_fill_time_seconds_calculated(self):
        """fill_time_seconds = fill_timestamp - submission_timestamp."""
        t0 = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=2.5)

        metric = ExecutionMetric(
            order_id="MET-001",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("10"),
            submission_timestamp=t0,
            expected_price=Decimal("450.00"),
            fill_timestamp=t1,
            fill_price=Decimal("450.10"),
        )
        metric.calculate_metrics()

        assert metric.fill_time_seconds == pytest.approx(2.5)

    def test_slippage_bps_calculated(self):
        """slippage_bps = (fill - expected) / expected * 10000 for BUY."""
        t0 = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1)

        metric = ExecutionMetric(
            order_id="MET-002",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("100"),
            submission_timestamp=t0,
            expected_price=Decimal("100.00"),
            fill_timestamp=t1,
            fill_price=Decimal("100.05"),
        )
        metric.calculate_metrics()

        # 0.05 / 100 * 10000 = 5.0 bps
        assert metric.slippage_bps is not None
        assert float(metric.slippage_bps) == pytest.approx(5.0)

    def test_no_metrics_without_fill(self):
        """Metrics are None if fill_timestamp or fill_price not set."""
        t0 = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)

        metric = ExecutionMetric(
            order_id="MET-003",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("10"),
            submission_timestamp=t0,
            expected_price=Decimal("450.00"),
        )
        metric.calculate_metrics()

        assert metric.fill_time_seconds is None
        assert metric.slippage_bps is None


# ---------------------------------------------------------------------------
# 5-7. is_order_stale TTL detection
# ---------------------------------------------------------------------------

class TestOrderStaleness:

    def _make_engine(self):
        """Build minimal engine for stale-order tests."""
        from unittest.mock import MagicMock
        from core.execution.engine import OrderExecutionEngine

        engine = OrderExecutionEngine(
            broker=MagicMock(),
            state_machine=MagicMock(),
            position_store=MagicMock(),
        )
        return engine

    def test_stale_when_exceeds_ttl(self):
        """is_order_stale → True when age >= TTL."""
        engine = self._make_engine()

        # Inject metadata with old timestamp
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=100)
        engine._order_metadata["ORD-STALE"] = {"submitted_at": old_ts}

        assert engine.is_order_stale("ORD-STALE", ttl_seconds=90) is True

    def test_not_stale_when_within_ttl(self):
        """is_order_stale → False when age < TTL."""
        engine = self._make_engine()

        fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        engine._order_metadata["ORD-FRESH"] = {"submitted_at": fresh_ts}

        assert engine.is_order_stale("ORD-FRESH", ttl_seconds=90) is False

    def test_not_stale_when_no_metadata(self):
        """is_order_stale → False when order has no metadata."""
        engine = self._make_engine()

        assert engine.is_order_stale("ORD-UNKNOWN", ttl_seconds=90) is False
