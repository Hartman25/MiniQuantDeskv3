"""
P1 Patch 2 – Duplicate Order Protection Across Restarts (strict tests)

INVARIANT:
    After crash+restart, OrderExecutionEngine MUST seed _submitted_order_ids
    from the TransactionLog so that previously submitted internal_order_ids
    are rejected with DuplicateOrderError.

These tests complement test_patch2_duplicate_order_guard.py with:
    - Explicit backward-compat: no log → first submit succeeds, second blocked
    - Edge: empty log → no IDs seeded
    - Edge: log with only non-SUBMIT events → no IDs seeded
    - Engine constructor signature: transaction_log is optional kwarg
"""

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


class TestDuplicateOrderRestartEdgeCases:

    def test_engine_constructor_accepts_transaction_log_kwarg(self, tmp_path):
        """OrderExecutionEngine.__init__ must accept transaction_log as keyword arg."""
        import inspect
        from core.execution.engine import OrderExecutionEngine

        sig = inspect.signature(OrderExecutionEngine.__init__)
        assert "transaction_log" in sig.parameters, (
            "OrderExecutionEngine.__init__ must accept transaction_log parameter"
        )

    def test_empty_transaction_log_seeds_nothing(self, tmp_path):
        """An empty log file should result in zero seeded IDs."""
        from core.state.transaction_log import TransactionLog
        from core.execution.engine import OrderExecutionEngine
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore

        log_path = tmp_path / "empty.ndjson"
        txlog = TransactionLog(path=log_path)
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

        assert len(engine._submitted_order_ids) == 0

        txlog2.close()
        sm_log.close()
        bus.stop()
        ps.close()

    def test_non_submit_events_not_seeded(self, tmp_path):
        """FILL, CANCEL, ERROR events must NOT seed the duplicate guard."""
        from core.state.transaction_log import TransactionLog
        from core.execution.engine import OrderExecutionEngine
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore

        log_path = tmp_path / "fills.ndjson"
        txlog = TransactionLog(path=log_path)
        txlog.append({"event_type": "FILL", "internal_order_id": "ORD-fill-1"})
        txlog.append({"event_type": "CANCEL", "internal_order_id": "ORD-cancel-1"})
        txlog.append({"event_type": "ERROR", "internal_order_id": "ORD-err-1"})
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

        assert len(engine._submitted_order_ids) == 0
        assert "ORD-fill-1" not in engine._submitted_order_ids
        assert "ORD-cancel-1" not in engine._submitted_order_ids

        txlog2.close()
        sm_log.close()
        bus.stop()
        ps.close()

    def test_backward_compat_no_log_duplicate_still_blocked_in_session(self, tmp_path):
        """Without a log, first submit succeeds, second same ID is blocked."""
        from core.execution.engine import OrderExecutionEngine, DuplicateOrderError
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

        # create order in state machine first
        sm.create_order(
            order_id="ORD-dup-sess",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        bid = engine.submit_market_order(
            internal_order_id="ORD-dup-sess",
            symbol="SPY",
            quantity=Decimal("5"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrat",
        )
        assert bid is not None

        # Same ID again in same session → DuplicateOrderError
        with pytest.raises(DuplicateOrderError):
            engine.submit_market_order(
                internal_order_id="ORD-dup-sess",
                symbol="SPY",
                quantity=Decimal("5"),
                side=BrokerOrderSide.BUY,
                strategy="TestStrat",
            )

        sm_log.close()
        bus.stop()
        ps.close()

    def test_multiple_submits_same_id_all_seeded(self, tmp_path):
        """If the log has duplicate ORDER_SUBMIT entries for the same ID,
        the set still contains it (idempotent)."""
        from core.state.transaction_log import TransactionLog
        from core.execution.engine import OrderExecutionEngine
        from tests.fixtures.runtime_harness import StubBrokerConnector
        from core.state.position_store import PositionStore

        log_path = tmp_path / "dup_submits.ndjson"
        txlog = TransactionLog(path=log_path)
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "ORD-x"})
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "ORD-x"})
        txlog.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "ORD-y"})
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

        assert "ORD-x" in engine._submitted_order_ids
        assert "ORD-y" in engine._submitted_order_ids
        assert len(engine._submitted_order_ids) == 2  # set deduplicates

        txlog2.close()
        sm_log.close()
        bus.stop()
        ps.close()
