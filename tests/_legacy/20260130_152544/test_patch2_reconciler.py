from __future__ import annotations

from unittest.mock import Mock
from core.state.reconciler import StartupReconciler


def test_startup_reconciler_detects_missing_position_and_order():
    broker = Mock()
    broker.get_positions.return_value = [{"symbol": "SPY", "qty": "1"}]
    broker.get_orders.return_value = [{"id": "B1", "symbol": "SPY", "status": "new"}]

    position_store = Mock()
    position_store.list_positions.return_value = []  # local empty

    order_tracker = Mock()
    order_tracker.get_open_orders.return_value = []  # local empty

    r = StartupReconciler(broker=broker, position_store=position_store, order_tracker=order_tracker)
    ds = r.reconcile_startup()

    types = {d.type for d in ds}
    assert "missing_position" in types
    assert "order_missing_local" in types


def test_startup_reconciler_no_discrepancies_when_aligned():
    broker = Mock()
    broker.get_positions.return_value = [{"symbol": "SPY", "qty": "1"}]
    broker.get_orders.return_value = [{"id": "B1", "symbol": "SPY", "status": "new"}]

    position_store = Mock()
    position_store.list_positions.return_value = [{"symbol": "SPY", "quantity": "1"}]

    order_tracker = Mock()
    order_tracker.get_open_orders.return_value = [{"broker_order_id": "B1", "symbol": "SPY", "status": "new"}]

    r = StartupReconciler(broker=broker, position_store=position_store, order_tracker=order_tracker)
    ds = r.reconcile_startup()

    assert ds == []
