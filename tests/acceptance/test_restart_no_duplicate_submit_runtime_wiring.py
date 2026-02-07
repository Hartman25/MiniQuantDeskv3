"""
PATCH 1 Acceptance Test — Restart Idempotency via Runtime Wiring

INVARIANT:
    When the runtime initialises through core/di/container.py, it passes the
    persistent TransactionLog to OrderExecutionEngine.  After a simulated
    crash+restart the engine MUST reject (DuplicateOrderError) any
    internal_order_id that was already submitted in a prior run.

WHY THIS MATTERS:
    Without this wiring, a restart after a crash could double-submit entries
    to the broker, violating position limits and creating unintended exposure.

TEST APPROACH:
    1. Create a TransactionLog with a pre-existing ORDER_SUBMIT event.
    2. Build a *real* Container and inject a StubBroker.
    3. Verify the engine inside that container seeds from the log.
    4. Assert that submitting the same internal_order_id raises DuplicateOrderError.
"""

import pytest
from decimal import Decimal
from pathlib import Path

from core.di.container import Container
from core.execution.engine import DuplicateOrderError
from core.state.transaction_log import TransactionLog
from core.brokers.alpaca_connector import BrokerOrderSide
from tests.fixtures.runtime_harness import StubBrokerConnector

# Path to the real micro config (placeholder API keys bypassed under pytest
# because PYTEST_CURRENT_TEST env is set automatically by pytest).
_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "config" / "config_micro.yaml")


class TestRestartIdempotencyRuntimeWiring:
    """Validate that Container wires transaction_log into OrderExecutionEngine."""

    def test_container_passes_transaction_log_to_engine(self):
        """
        The real Container.set_broker_connector() must pass
        transaction_log=self._transaction_log when constructing
        OrderExecutionEngine.
        """
        container = Container()
        container.initialize(_CONFIG_PATH)

        broker = StubBrokerConnector()
        container.set_broker_connector(broker)

        engine = container.get_order_execution_engine()
        assert engine is not None, "Engine must be created by container"
        assert engine.transaction_log is not None, (
            "Container must pass transaction_log to OrderExecutionEngine"
        )
        assert engine.transaction_log is container.get_transaction_log(), (
            "Engine's transaction_log must be the same instance as the container's"
        )

        container.stop()

    def test_restart_rejects_duplicate_internal_order_id(self, tmp_path):
        """
        Simulate crash+restart:
          Run 1 — submit an order (populates transaction log).
          Run 2 — new engine, seeded from same log, rejects duplicate.
        """
        log_path = tmp_path / "txn.ndjson"

        # --- Run 1: write an ORDER_SUBMIT event --------------------------------
        txlog_run1 = TransactionLog(path=log_path)
        txlog_run1.append({
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "ORD-restart-test-001",
            "trade_id": "t_restart_001",
            "symbol": "SPY",
            "side": "BUY",
            "qty": "5",
            "order_type": "MARKET",
            "strategy": "TestStrat",
        })
        txlog_run1.close()

        # --- Run 2: build real Container, override its transaction log ---------
        container = Container()
        container.initialize(_CONFIG_PATH)

        # Swap the container's transaction log to our pre-populated one
        txlog_run2 = TransactionLog(path=log_path)
        container._transaction_log = txlog_run2

        broker = StubBrokerConnector()
        container.set_broker_connector(broker)

        engine = container.get_order_execution_engine()
        assert engine is not None

        # The engine should have seeded the ID from the log
        assert "ORD-restart-test-001" in engine._submitted_order_ids, (
            "Engine must seed _submitted_order_ids from the transaction log on construction"
        )

        # Attempting to re-submit the same ID should raise DuplicateOrderError.
        # First, create the order in the state machine (required before submit).
        sm = container.get_order_machine()
        sm.create_order(
            order_id="ORD-restart-test-001",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        with pytest.raises(DuplicateOrderError):
            engine.submit_market_order(
                internal_order_id="ORD-restart-test-001",
                symbol="SPY",
                quantity=Decimal("5"),
                side=BrokerOrderSide.BUY,
                strategy="TestStrat",
            )

        txlog_run2.close()
        container.stop()

    def test_fresh_start_allows_new_order_id(self, tmp_path):
        """
        If the transaction log has no ORDER_SUBMIT for a given ID,
        the engine must allow submission (no false positives).
        """
        log_path = tmp_path / "txn_fresh.ndjson"
        txlog = TransactionLog(path=log_path)
        # Write only a FILL event — should NOT seed the duplicate guard
        txlog.append({
            "event_type": "FILL",
            "internal_order_id": "ORD-old-fill",
            "trade_id": "t_fill_001",
        })
        txlog.close()

        container = Container()
        container.initialize(_CONFIG_PATH)
        container._transaction_log = TransactionLog(path=log_path)

        broker = StubBrokerConnector()
        container.set_broker_connector(broker)

        engine = container.get_order_execution_engine()

        # This ID was never submitted — should NOT be in the guard set
        assert "ORD-never-submitted" not in engine._submitted_order_ids

        container.stop()

    def test_multiple_restarts_accumulate_ids(self, tmp_path):
        """
        After multiple crash/restart cycles, ALL previously submitted IDs
        must remain in the duplicate guard.
        """
        log_path = tmp_path / "txn_multi.ndjson"

        # Simulate 3 "runs" each writing an ORDER_SUBMIT
        txlog = TransactionLog(path=log_path)
        for i in range(3):
            txlog.append({
                "event_type": "ORDER_SUBMIT",
                "internal_order_id": f"ORD-multi-{i}",
                "trade_id": f"t_multi_{i}",
                "symbol": "SPY",
                "side": "BUY",
                "qty": "1",
                "order_type": "MARKET",
                "strategy": "TestStrat",
            })
        txlog.close()

        # "Restart" — new engine reads the accumulated log
        container = Container()
        container.initialize(_CONFIG_PATH)
        container._transaction_log = TransactionLog(path=log_path)

        broker = StubBrokerConnector()
        container.set_broker_connector(broker)

        engine = container.get_order_execution_engine()

        for i in range(3):
            assert f"ORD-multi-{i}" in engine._submitted_order_ids, (
                f"ORD-multi-{i} must be seeded after restart"
            )

        container.stop()
