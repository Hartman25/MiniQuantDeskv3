"""
PATCH 6 — Persist and restore pending orders into the order state machine

INVARIANT:
    On restart, any SUBMITTED-but-not-FILLED orders are loaded back into
    the state machine from the transaction log.  Terminal orders (FILLED,
    CANCELLED, REJECTED, EXPIRED) are NOT restored.

TESTS:
    1. Submitted order survives simulated crash and is restored.
    2. Filled order is NOT restored (terminal).
    3. Cancelled order is NOT restored (terminal).
    4. Multiple orders: only non-terminal ones restored.
    5. Idempotent: calling restore twice doesn't duplicate.
    6. Partially-filled order IS restored.
    7. Empty log → 0 restored.
    8. Metadata (symbol, strategy, broker_order_id) survives round-trip.
"""

import pytest
from decimal import Decimal
from pathlib import Path

from core.state.order_machine import OrderStateMachine, OrderStatus, Order
from core.state.transaction_log import TransactionLog
from core.events.bus import OrderEventBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_machine_and_log(tmp_path: Path):
    """Create a fresh OrderStateMachine + TransactionLog pair."""
    log_path = tmp_path / "orders.ndjson"
    tlog = TransactionLog(path=log_path)
    bus = OrderEventBus()
    bus.start()  # Must start before emitting events
    machine = OrderStateMachine(event_bus=bus, transaction_log=tlog)
    return machine, tlog


def _submit_order(machine, order_id, symbol="SPY", strategy="TestStrat",
                  qty="10", side="BUY", order_type="MARKET",
                  broker_order_id="BRK-001"):
    """Create an order and transition it to SUBMITTED."""
    machine.create_order(
        order_id=order_id,
        symbol=symbol,
        quantity=Decimal(qty),
        side=side,
        order_type=order_type,
        strategy=strategy,
    )
    machine.transition(
        order_id=order_id,
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id=broker_order_id,
    )
    return broker_order_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRestorePendingOrders:
    """Core restore-on-restart tests."""

    def test_submitted_order_restored(self, tmp_path):
        """A SUBMITTED order survives simulated crash."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(m1, "ORD-1")

        # Verify it's in the first machine
        assert m1.get_order("ORD-1").state == OrderStatus.SUBMITTED

        # Simulate crash: new machine, same log
        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        assert m2.get_order("ORD-1") is None  # empty before restore

        restored = m2.restore_pending_orders(tlog)
        assert restored == 1

        order = m2.get_order("ORD-1")
        assert order is not None
        assert order.state == OrderStatus.SUBMITTED

    def test_filled_order_not_restored(self, tmp_path):
        """A FILLED order is terminal and must NOT be restored."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(m1, "ORD-2")
        m1.transition(
            order_id="ORD-2",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK-002",
            filled_qty=Decimal("10"),
            fill_price=Decimal("100"),
        )

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        restored = m2.restore_pending_orders(tlog)
        assert restored == 0
        assert m2.get_order("ORD-2") is None

    def test_cancelled_order_not_restored(self, tmp_path):
        """A CANCELLED order is terminal and must NOT be restored."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(m1, "ORD-3")
        m1.transition(
            order_id="ORD-3",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.CANCELLED,
            broker_order_id="BRK-003",
            reason="user_cancel",
        )

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        restored = m2.restore_pending_orders(tlog)
        assert restored == 0

    def test_multiple_orders_mixed(self, tmp_path):
        """Only non-terminal orders are restored from a mixed log."""
        m1, tlog = _make_machine_and_log(tmp_path)

        # Order A: SUBMITTED (should restore)
        _submit_order(m1, "ORD-A", broker_order_id="BRK-A")

        # Order B: FILLED (should NOT restore)
        _submit_order(m1, "ORD-B", broker_order_id="BRK-B")
        m1.transition(
            order_id="ORD-B",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK-B",
            filled_qty=Decimal("10"),
            fill_price=Decimal("99"),
        )

        # Order C: SUBMITTED (should restore)
        _submit_order(m1, "ORD-C", symbol="AAPL", broker_order_id="BRK-C")

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        restored = m2.restore_pending_orders(tlog)
        assert restored == 2

        assert m2.get_order("ORD-A") is not None
        assert m2.get_order("ORD-B") is None
        assert m2.get_order("ORD-C") is not None

    def test_idempotent(self, tmp_path):
        """Calling restore twice doesn't duplicate orders."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(m1, "ORD-IDEM")

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        r1 = m2.restore_pending_orders(tlog)
        r2 = m2.restore_pending_orders(tlog)

        assert r1 == 1
        assert r2 == 0  # already present

        pending = m2.get_pending_orders()
        assert len(pending) == 1

    def test_partially_filled_restored(self, tmp_path):
        """PARTIALLY_FILLED is non-terminal and should be restored."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(m1, "ORD-PF")
        m1.transition(
            order_id="ORD-PF",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.PARTIALLY_FILLED,
            broker_order_id="BRK-PF",
            filled_qty=Decimal("3"),
            fill_price=Decimal("100"),
        )

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        restored = m2.restore_pending_orders(tlog)
        assert restored == 1

        order = m2.get_order("ORD-PF")
        assert order is not None
        assert order.state == OrderStatus.PARTIALLY_FILLED

    def test_empty_log_restores_nothing(self, tmp_path):
        """Empty log → 0 restored."""
        log_path = tmp_path / "empty.ndjson"
        tlog = TransactionLog(path=log_path)
        bus = OrderEventBus()
        m = OrderStateMachine(event_bus=bus, transaction_log=tlog)

        restored = m.restore_pending_orders(tlog)
        assert restored == 0

    def test_metadata_survives_roundtrip(self, tmp_path):
        """Symbol, strategy, broker_order_id survive restore."""
        m1, tlog = _make_machine_and_log(tmp_path)
        _submit_order(
            m1, "ORD-META",
            symbol="TSLA",
            strategy="VWAPMicro",
            broker_order_id="BRK-META-42",
        )

        bus2 = OrderEventBus()
        m2 = OrderStateMachine(event_bus=bus2, transaction_log=tlog)
        m2.restore_pending_orders(tlog)

        order = m2.get_order("ORD-META")
        assert order is not None
        assert order.symbol == "TSLA"
        assert order.strategy == "VWAPMicro"
        assert order.broker_order_id == "BRK-META-42"
