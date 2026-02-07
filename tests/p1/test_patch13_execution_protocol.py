"""
PATCH 13 — Make backtest and live share same execution interface

INVARIANT:
    A single ``ExecutionProtocol`` defines the methods every execution
    backend must implement.  ``NullExecution`` satisfies the protocol
    for testing.  ``isinstance(obj, ExecutionProtocol)`` works at runtime.

TESTS:
    1.  NullExecution satisfies ExecutionProtocol (isinstance check).
    2.  submit_market_order returns a broker_order_id string.
    3.  submit_limit_order returns a broker_order_id string.
    4.  submit_stop_order returns a broker_order_id string.
    5.  cancel_order returns True for existing order.
    6.  cancel_order returns False for unknown order.
    7.  get_order_status returns status string.
    8.  get_fill_details for market order returns (qty, price).
    9.  get_fill_details for unknown returns (None, None).
   10.  Market orders auto-fill with status FILLED.
   11.  Limit orders start with status SUBMITTED.
   12.  Cancel changes status to CANCELLED.
   13.  ExecutionProtocol is runtime_checkable.
   14.  Protocol detects non-compliant object.
   15.  Multiple orders get unique broker IDs.
"""

from decimal import Decimal
from typing import Optional, Tuple

import pytest

from core.execution.protocol import ExecutionProtocol, NullExecution


class TestExecutionProtocol:

    def test_null_execution_satisfies_protocol(self):
        """NullExecution must pass isinstance check."""
        eng = NullExecution()
        assert isinstance(eng, ExecutionProtocol)

    def test_submit_market_order_returns_str(self):
        eng = NullExecution()
        bid = eng.submit_market_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY", strategy="test",
        )
        assert isinstance(bid, str)
        assert bid.startswith("NULL-")

    def test_submit_limit_order_returns_str(self):
        eng = NullExecution()
        bid = eng.submit_limit_order(
            internal_order_id="ORD-2", symbol="AAPL",
            quantity=Decimal("5"), side="BUY",
            limit_price=Decimal("150"), strategy="test",
        )
        assert isinstance(bid, str)

    def test_submit_stop_order_returns_str(self):
        eng = NullExecution()
        bid = eng.submit_stop_order(
            internal_order_id="ORD-3", symbol="TSLA",
            quantity=Decimal("3"), side="SELL",
            stop_price=Decimal("200"), strategy="test",
        )
        assert isinstance(bid, str)

    def test_cancel_existing_returns_true(self):
        eng = NullExecution()
        bid = eng.submit_market_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY", strategy="test",
        )
        assert eng.cancel_order("ORD-1", bid) is True

    def test_cancel_unknown_returns_false(self):
        eng = NullExecution()
        assert eng.cancel_order("NONEXIST", "BRK-999") is False

    def test_get_order_status(self):
        eng = NullExecution()
        bid = eng.submit_market_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY", strategy="test",
        )
        status = eng.get_order_status("ORD-1", bid)
        assert status == "FILLED"

    def test_get_fill_details_market(self):
        eng = NullExecution()
        eng.submit_market_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY", strategy="test",
        )
        qty, price = eng.get_fill_details("ORD-1")
        assert qty == Decimal("10")
        assert price == Decimal("100")

    def test_get_fill_details_unknown(self):
        eng = NullExecution()
        qty, price = eng.get_fill_details("NONEXIST")
        assert qty is None
        assert price is None

    def test_market_order_auto_fills(self):
        eng = NullExecution()
        bid = eng.submit_market_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY", strategy="test",
        )
        assert eng.get_order_status("ORD-1", bid) == "FILLED"

    def test_limit_order_starts_submitted(self):
        eng = NullExecution()
        bid = eng.submit_limit_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY",
            limit_price=Decimal("100"), strategy="test",
        )
        assert eng.get_order_status("ORD-1", bid) == "SUBMITTED"

    def test_cancel_changes_status(self):
        eng = NullExecution()
        bid = eng.submit_limit_order(
            internal_order_id="ORD-1", symbol="SPY",
            quantity=Decimal("10"), side="BUY",
            limit_price=Decimal("100"), strategy="test",
        )
        eng.cancel_order("ORD-1", bid)
        assert eng.get_order_status("ORD-1", bid) == "CANCELLED"

    def test_protocol_is_runtime_checkable(self):
        """ExecutionProtocol can be used with isinstance at runtime."""
        assert hasattr(ExecutionProtocol, "__protocol_attrs__") or \
               hasattr(ExecutionProtocol, "_is_runtime_protocol")

    def test_non_compliant_object_fails(self):
        """A plain object should NOT satisfy ExecutionProtocol."""

        class Empty:
            pass

        assert not isinstance(Empty(), ExecutionProtocol)

    def test_unique_broker_ids(self):
        eng = NullExecution()
        ids = set()
        for i in range(10):
            bid = eng.submit_market_order(
                internal_order_id=f"ORD-{i}", symbol="SPY",
                quantity=Decimal("1"), side="BUY", strategy="test",
            )
            ids.add(bid)
        assert len(ids) == 10
