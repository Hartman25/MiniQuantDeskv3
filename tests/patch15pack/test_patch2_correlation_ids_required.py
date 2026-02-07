"""
PATCH 2 — Correlation IDs Required

INVARIANT:
    Every order lifecycle event must carry trade_id + internal_order_id +
    broker_order_id (once known).  Fill events must appear in BOTH the
    TradeJournal and the TransactionLog with matching correlation IDs.

TESTS:
    1. Engine.register_trade_id() links signal trade_id to internal_order_id.
    2. ORDER_SUBMIT in trade_journal carries registered trade_id.
    3. ORDER_FILLED in trade_journal carries matching trade_id + broker_order_id.
    4. ORDER_FILLED in transaction_log carries matching IDs.
    5. TradeJournal rejects events missing required correlation fields.
    6. TransactionLog rejects ORDER_* events missing internal_order_id.
"""

import pytest
from decimal import Decimal
from pathlib import Path

from core.events.bus import OrderEventBus
from core.execution.engine import OrderExecutionEngine, DuplicateOrderError
from core.state.transaction_log import TransactionLog
from core.state.order_machine import OrderStateMachine
from core.state.position_store import PositionStore
from core.journal.trade_journal import TradeJournal, TradeIds, build_trade_event
from core.brokers.alpaca_connector import BrokerOrderSide
from tests.fixtures.runtime_harness import StubBrokerConnector


def _make_engine(tmp_path, *, broker=None):
    """Build a real engine with journal + log wired."""
    bus = OrderEventBus()
    bus.start()
    txlog = TransactionLog(path=tmp_path / "txn.ndjson")
    sm = OrderStateMachine(event_bus=bus, transaction_log=txlog)
    ps = PositionStore(db_path=tmp_path / "pos.db")

    if broker is None:
        broker = StubBrokerConnector()

    trade_journal = TradeJournal(base_dir=tmp_path / "journal")

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=sm,
        position_store=ps,
        transaction_log=txlog,
    )
    engine.set_trade_journal(trade_journal, run_id="run_test_001")

    return engine, txlog, trade_journal, sm, bus, ps


class TestRegisterTradeId:
    """Engine.register_trade_id() links signal trade_id to internal_order_id."""

    def test_registered_trade_id_used_in_journal_events(self, tmp_path):
        engine, txlog, tj, sm, bus, ps = _make_engine(tmp_path)

        # Register a specific trade_id before submission
        engine.register_trade_id("ORD-corr-001", "t_signal_abc")

        sm.create_order(
            order_id="ORD-corr-001",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        broker_id = engine.submit_market_order(
            internal_order_id="ORD-corr-001",
            symbol="SPY",
            quantity=Decimal("5"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrat",
        )

        # Read trade journal events
        events = list(tj.iter_events())
        submit_events = [e for e in events if e.get("event_type") == "ORDER_SUBMIT"]

        assert len(submit_events) >= 1, "Expected ORDER_SUBMIT in trade journal"
        assert submit_events[0]["trade_id"] == "t_signal_abc", (
            "Engine must use the registered trade_id, not auto-generate one"
        )
        assert submit_events[0]["internal_order_id"] == "ORD-corr-001"
        assert submit_events[0]["broker_order_id"] == broker_id

        tj.close()
        txlog.close()
        bus.stop()
        ps.close()

    def test_unregistered_trade_id_is_auto_generated(self, tmp_path):
        engine, txlog, tj, sm, bus, ps = _make_engine(tmp_path)

        # Do NOT register — engine should auto-generate
        sm.create_order(
            order_id="ORD-auto-001",
            symbol="SPY",
            quantity=Decimal("1"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        engine.submit_market_order(
            internal_order_id="ORD-auto-001",
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrat",
        )

        events = list(tj.iter_events())
        submit_events = [e for e in events if e.get("event_type") == "ORDER_SUBMIT"]
        assert len(submit_events) >= 1
        # Auto-generated trade_id starts with "t_"
        assert submit_events[0]["trade_id"].startswith("t_")

        tj.close()
        txlog.close()
        bus.stop()
        ps.close()


class TestFillCorrelationIds:
    """Fill events carry all three correlation IDs."""

    def test_fill_event_in_trade_journal(self, tmp_path):
        """ORDER_FILLED must appear in trade journal with correct IDs."""
        engine, txlog, tj, sm, bus, ps = _make_engine(tmp_path)

        engine.register_trade_id("ORD-fill-001", "t_fill_journal")

        sm.create_order(
            order_id="ORD-fill-001",
            symbol="SPY",
            quantity=Decimal("3"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        broker_id = engine.submit_market_order(
            internal_order_id="ORD-fill-001",
            symbol="SPY",
            quantity=Decimal("3"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrat",
        )

        # StubBroker immediately fills → wait_for_order processes fill
        engine.wait_for_order(
            internal_order_id="ORD-fill-001",
            broker_order_id=broker_id,
            timeout_seconds=2,
            poll_interval=0.1,
        )

        events = list(tj.iter_events())
        fill_events = [e for e in events if e.get("event_type") == "ORDER_FILLED"]

        assert len(fill_events) >= 1, "Expected ORDER_FILLED in trade journal"
        fe = fill_events[0]
        assert fe["trade_id"] == "t_fill_journal"
        assert fe["internal_order_id"] == "ORD-fill-001"
        assert fe["broker_order_id"] == broker_id

        tj.close()
        txlog.close()
        bus.stop()
        ps.close()

    def test_fill_event_in_transaction_log(self, tmp_path):
        """ORDER_FILLED must appear in transaction log with correct IDs."""
        engine, txlog, tj, sm, bus, ps = _make_engine(tmp_path)

        engine.register_trade_id("ORD-fill-002", "t_fill_txlog")

        sm.create_order(
            order_id="ORD-fill-002",
            symbol="SPY",
            quantity=Decimal("2"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        broker_id = engine.submit_market_order(
            internal_order_id="ORD-fill-002",
            symbol="SPY",
            quantity=Decimal("2"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrat",
        )

        engine.wait_for_order(
            internal_order_id="ORD-fill-002",
            broker_order_id=broker_id,
            timeout_seconds=2,
            poll_interval=0.1,
        )

        txlog.close()
        # Re-open to read
        txlog2 = TransactionLog(path=tmp_path / "txn.ndjson")
        all_events = list(txlog2.iter_events())
        fill_events = [
            e for e in all_events
            if (e.get("event_type") or "").upper() == "ORDER_FILLED"
        ]

        assert len(fill_events) >= 1, "Expected ORDER_FILLED in transaction log"
        fe = fill_events[0]
        assert fe["trade_id"] == "t_fill_txlog"
        assert fe["internal_order_id"] == "ORD-fill-002"
        assert fe["broker_order_id"] == broker_id

        txlog2.close()
        tj.close()
        bus.stop()
        ps.close()


class TestCorrelationIdValidation:
    """Journal and log enforce required fields."""

    def test_trade_journal_rejects_missing_trade_id(self, tmp_path):
        """TradeJournal.emit() must reject events without trade_id."""
        tj = TradeJournal(base_dir=tmp_path / "journal")
        with pytest.raises(ValueError, match="correlation"):
            tj.emit({
                "event_type": "ORDER_SUBMIT",
                "internal_order_id": "ORD-123",
                # missing trade_id
            })
        tj.close()

    def test_trade_journal_rejects_missing_internal_order_id(self, tmp_path):
        """TradeJournal.emit() must reject events without internal_order_id."""
        tj = TradeJournal(base_dir=tmp_path / "journal")
        with pytest.raises(ValueError, match="correlation"):
            tj.emit({
                "event_type": "ORDER_SUBMIT",
                "trade_id": "t_123",
                # missing internal_order_id
            })
        tj.close()

    def test_transaction_log_rejects_order_event_without_internal_id(self, tmp_path):
        """TransactionLog.append() must reject ORDER_* without internal_order_id."""
        from core.state.transaction_log import TransactionLogError
        txlog = TransactionLog(path=tmp_path / "txn.ndjson")
        # ValueError is raised internally, then wrapped in TransactionLogError
        with pytest.raises((ValueError, TransactionLogError)):
            txlog.append({
                "event_type": "ORDER_SUBMIT",
                # missing internal_order_id
            })
        txlog.close()
