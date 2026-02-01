"""
P1 Patch 2 – No Duplicate Orders After Restart

INVARIANT:
    After a crash and restart, the OrderExecutionEngine must refuse to
    re-submit an internal_order_id that was already submitted in a
    previous run, provided a TransactionLog is available.

WHY THIS MATTERS:
    _submitted_order_ids is an in-memory set.  After restart it's empty,
    so the duplicate-order guard (DuplicateOrderError) has no memory.
    If old signals are replayed or the same trade_id is regenerated,
    a second order for the same internal ID would reach the broker.

DESIGN:
    OrderExecutionEngine gains an optional `transaction_log` parameter.
    On construction it scans the log for ORDER_SUBMIT events and seeds
    _submitted_order_ids with their internal_order_id values.
"""

import json
import pytest
from decimal import Decimal
from pathlib import Path


def _make_state_machine(tmp_path):
    """Create a minimal OrderStateMachine with required dependencies."""
    from core.events.bus import OrderEventBus
    from core.state.transaction_log import TransactionLog
    from core.state.order_machine import OrderStateMachine

    bus = OrderEventBus()
    bus.start()
    sm_log = TransactionLog(path=tmp_path / "sm_txn.ndjson")
    sm = OrderStateMachine(event_bus=bus, transaction_log=sm_log)
    return sm, bus, sm_log


class TestDuplicateOrderGuardAcrossRestart:
    """Test that _submitted_order_ids survives restart via TransactionLog."""

    def test_engine_seeds_submitted_ids_from_transaction_log(self, tmp_path):
        """
        Write ORDER_SUBMIT events to a transaction log, then construct
        a new OrderExecutionEngine with that log.  The engine's
        _submitted_order_ids must contain the previously-submitted IDs.
        """
        from core.state.transaction_log import TransactionLog
        from core.execution.engine import OrderExecutionEngine
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore

        log_path = tmp_path / "txn.ndjson"
        txlog = TransactionLog(path=log_path)

        # Simulate events from a previous run
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "STRAT-aaa111"})
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "STRAT-bbb222"})
        txlog.append({"event_type": "FILL", "internal_order_id": "STRAT-aaa111"})  # not a submit
        txlog.close()

        # Re-open for reading
        txlog2 = TransactionLog(path=log_path)

        broker = StubBrokerConnector()
        sm, bus, sm_log = _make_state_machine(tmp_path)
        ps = PositionStore(db_path=tmp_path / "pos.db")

        engine = OrderExecutionEngine(
            broker=broker,
            state_machine=sm,
            position_store=ps,
            transaction_log=txlog2,
        )

        assert "STRAT-aaa111" in engine._submitted_order_ids
        assert "STRAT-bbb222" in engine._submitted_order_ids
        # FILL events should NOT be seeded
        assert len(engine._submitted_order_ids) == 2

        txlog2.close()
        sm_log.close()
        bus.stop()
        ps.close()

    def test_engine_rejects_duplicate_after_restart(self, tmp_path):
        """
        After seeding from the log, submitting a market order with a
        previously-used internal_order_id must raise DuplicateOrderError.
        """
        from core.state.transaction_log import TransactionLog
        from core.execution.engine import OrderExecutionEngine, DuplicateOrderError
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore
        from core.brokers.alpaca_connector import BrokerOrderSide

        log_path = tmp_path / "txn.ndjson"
        txlog = TransactionLog(path=log_path)
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "STRAT-dup123"})
        txlog.close()

        txlog2 = TransactionLog(path=log_path)

        broker = StubBrokerConnector()
        sm, bus, sm_log = _make_state_machine(tmp_path)
        ps = PositionStore(db_path=tmp_path / "pos.db")

        engine = OrderExecutionEngine(
            broker=broker,
            state_machine=sm,
            position_store=ps,
            transaction_log=txlog2,
        )

        with pytest.raises(DuplicateOrderError):
            engine.submit_market_order(
                internal_order_id="STRAT-dup123",
                symbol="SPY",
                quantity=Decimal("10"),
                side=BrokerOrderSide.BUY,
                strategy="TestStrategy",
            )

        txlog2.close()
        sm_log.close()
        bus.stop()
        ps.close()

    def test_engine_works_without_transaction_log(self, tmp_path):
        """
        When no transaction_log is provided, the engine should work
        exactly as before (backward-compatible).
        """
        from core.execution.engine import OrderExecutionEngine
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore
        from core.brokers.alpaca_connector import BrokerOrderSide

        broker = StubBrokerConnector()
        sm, bus, sm_log = _make_state_machine(tmp_path)
        ps = PositionStore(db_path=tmp_path / "pos.db")

        engine = OrderExecutionEngine(
            broker=broker,
            state_machine=sm,
            position_store=ps,
            # no transaction_log
        )

        assert len(engine._submitted_order_ids) == 0

        # Should submit fine
        sm.create_order(
            order_id="STRAT-new1",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
        )
        bid = engine.submit_market_order(
            internal_order_id="STRAT-new1",
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrategy",
        )
        assert bid is not None
        sm_log.close()
        bus.stop()
        ps.close()
