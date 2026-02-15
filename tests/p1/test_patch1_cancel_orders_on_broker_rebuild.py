"""
Patch 1: Cancel all open broker orders before rebuilding state.

INVARIANT:
    During broker-based recovery rebuild (_recover_from_broker),
    all open orders must be cancelled before reconstructing positions.

WHY THIS MATTERS:
    Without cancelling orders, "ghost orders" can remain live after restart,
    causing unexpected fills and position drift.

DESIGN:
    - Before rebuilding positions in _recover_from_broker(), fetch open orders
    - Cancel all working orders (new, accepted, pending_new, partially_filled, held)
    - Paper mode: log failures but continue recovery
    - Live mode: halt recovery if any cancellation fails

TESTS:
    A. Paper mode: cancellation failures don't halt recovery
    B. Live mode: cancellation failures halt recovery
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestPatch1CancelOrdersOnBrokerRebuild:
    """Test order cancellation during broker-based recovery."""

    def test_paper_mode_continues_despite_cancel_failures(self, tmp_path):
        """
        Test A (paper mode):
        - Broker has 2 open orders
        - cancel succeeds for 1, fails for 1
        - Recovery continues and does not raise
        - broker.cancel_order called for both
        """
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        # Setup persistence (no saved state)
        persistence = StatePersistence(state_dir=tmp_path / "state")

        # Setup broker with 2 open orders
        order1 = MagicMock()
        order1.id = "ORDER-001"
        order1.symbol = "SPY"
        order1.status = "new"

        order2 = MagicMock()
        order2.id = "ORDER-002"
        order2.symbol = "AAPL"
        order2.status = "partially_filled"

        broker = MagicMock()
        broker.get_orders.return_value = [order1, order2]
        broker.get_positions.return_value = []

        # Cancel succeeds for order1, fails for order2
        def cancel_side_effect(order_id):
            if order_id == "ORDER-001":
                return True
            else:
                raise RuntimeError("Cancel failed for ORDER-002")

        broker.cancel_order.side_effect = cancel_side_effect

        position_store = MagicMock()
        order_machine = MagicMock()

        # Create coordinator in PAPER mode
        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True  # Paper mode
        )

        # Recovery should continue despite cancel failure
        report = coord.recover()

        # Verify recovery succeeded (didn't raise)
        assert report.status == RecoveryStatus.REBUILT

        # Verify both cancel attempts were made
        assert broker.cancel_order.call_count == 2
        broker.cancel_order.assert_any_call("ORDER-001")
        broker.cancel_order.assert_any_call("ORDER-002")

        # Verify only 1 order was successfully cancelled
        assert report.orders_cancelled == 1

    def test_live_mode_halts_on_cancel_failure(self, tmp_path):
        """
        Test B (live mode):
        - Broker has 1 open order
        - cancel raises
        - Recovery raises / returns FAILED
        - broker.cancel_order attempted
        """
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        # Setup persistence (no saved state)
        persistence = StatePersistence(state_dir=tmp_path / "state")

        # Setup broker with 1 open order
        order1 = MagicMock()
        order1.id = "ORDER-001"
        order1.symbol = "SPY"
        order1.status = "new"

        broker = MagicMock()
        broker.get_orders.return_value = [order1]
        broker.get_positions.return_value = []

        # Cancel fails
        broker.cancel_order.side_effect = RuntimeError("Cancel failed")

        position_store = MagicMock()
        order_machine = MagicMock()

        # Create coordinator in LIVE mode
        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=False  # Live mode
        )

        # Recovery should fail
        report = coord.recover()

        # Verify recovery failed (exception caught by recover())
        assert report.status == RecoveryStatus.FAILED

        # Verify cancel attempt was made
        broker.cancel_order.assert_called_once_with("ORDER-001")

    def test_no_open_orders_skips_cancellation(self, tmp_path):
        """
        When broker has no open orders, cancellation is skipped.
        """
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = StatePersistence(state_dir=tmp_path / "state")

        broker = MagicMock()
        broker.get_orders.return_value = []  # No orders
        broker.get_positions.return_value = []

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True
        )

        report = coord.recover()

        # Verify recovery succeeded
        assert report.status == RecoveryStatus.REBUILT

        # Verify no cancel calls
        broker.cancel_order.assert_not_called()

        # Verify 0 orders cancelled
        assert report.orders_cancelled == 0

    def test_only_working_orders_cancelled(self, tmp_path):
        """
        Only orders with working statuses (new, accepted, etc.) are cancelled.
        Filled/cancelled orders are skipped.
        """
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = StatePersistence(state_dir=tmp_path / "state")

        # Mix of working and completed orders
        order1 = MagicMock()
        order1.id = "ORDER-001"
        order1.symbol = "SPY"
        order1.status = "new"  # Working

        order2 = MagicMock()
        order2.id = "ORDER-002"
        order2.symbol = "AAPL"
        order2.status = "filled"  # Not working

        order3 = MagicMock()
        order3.id = "ORDER-003"
        order3.symbol = "TSLA"
        order3.status = "partially_filled"  # Working

        broker = MagicMock()
        broker.get_orders.return_value = [order1, order2, order3]
        broker.get_positions.return_value = []
        broker.cancel_order.return_value = True

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
            paper_mode=True
        )

        report = coord.recover()

        # Verify recovery succeeded
        assert report.status == RecoveryStatus.REBUILT

        # Verify only working orders (001, 003) were cancelled, not 002
        assert broker.cancel_order.call_count == 2
        broker.cancel_order.assert_any_call("ORDER-001")
        broker.cancel_order.assert_any_call("ORDER-003")

        # Verify 2 orders cancelled
        assert report.orders_cancelled == 2
