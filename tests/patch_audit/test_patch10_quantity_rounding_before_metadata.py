"""
PATCH 10 tests: Quantity rounding happens before metadata storage.

Problem: If quantity rounding occurs after metadata is stored, the metadata
contains the ORIGINAL (un-rounded) quantity while the broker receives the
ROUNDED quantity, creating an inconsistency.

Solution: Ensure rounding always happens before metadata storage and that
metadata always contains the quantity that was actually sent to the broker.

Tests:
1. Metadata contains rounded quantity (not original)
2. Broker receives same quantity as in metadata
3. Order without symbol_properties doesn't crash
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest


def test_metadata_contains_rounded_quantity(tmp_path):
    """PATCH 10: Metadata stores the rounded quantity, not the original."""
    from core.execution.engine import OrderExecutionEngine
    from core.brokers.alpaca_connector import BrokerOrderSide
    from core.state import OrderStateMachine, PositionStore
    from core.market.symbol_properties import SymbolProperties

    # Setup
    broker = MagicMock()
    event_bus = MagicMock()
    transaction_log = MagicMock()
    state_machine = OrderStateMachine(event_bus=event_bus, transaction_log=transaction_log)
    position_store = PositionStore(db_path=str(tmp_path / "positions.db"))

    # Mock symbol properties cache with lot_size=100 for SPY
    symbol_props_cache = MagicMock()
    spy_props = SymbolProperties(
        symbol="SPY",
        lot_size=100,
        min_order_size=1,
        max_order_size=10000,
        is_fractionable=False,
    )
    symbol_props_cache.get.return_value = spy_props

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
        symbol_properties=symbol_props_cache,
    )

    broker.submit_market_order.return_value = "BROKER-001"

    # Submit order with quantity=150 (not a multiple of 100)
    # Should round to 100 (nearest multiple of 100, rounding DOWN)
    internal_id = "TEST-001"
    engine.submit_market_order(
        internal_order_id=internal_id,
        symbol="SPY",
        quantity=Decimal("150"),
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # Check metadata contains ROUNDED quantity (100)
    metadata = engine._order_metadata[internal_id]
    assert metadata["quantity"] == Decimal("100"), f"Expected 100, got {metadata['quantity']}"

    # Check broker received ROUNDED quantity
    broker.submit_market_order.assert_called_once()
    call_args = broker.submit_market_order.call_args
    assert call_args.kwargs["quantity"] == Decimal("100")


def test_broker_and_metadata_quantities_match(tmp_path):
    """PATCH 10: Broker receives same quantity as stored in metadata."""
    from core.execution.engine import OrderExecutionEngine
    from core.brokers.alpaca_connector import BrokerOrderSide
    from core.state import OrderStateMachine, PositionStore
    from core.market.symbol_properties import SymbolProperties

    broker = MagicMock()
    event_bus = MagicMock()
    transaction_log = MagicMock()
    state_machine = OrderStateMachine(event_bus=event_bus, transaction_log=transaction_log)
    position_store = PositionStore(db_path=str(tmp_path / "positions.db"))

    symbol_props_cache = MagicMock()
    aapl_props = SymbolProperties(
        symbol="AAPL",
        lot_size=1,  # Lot size of 1 (no rounding effect expected)
        min_order_size=1,
        max_order_size=10000,
        is_fractionable=False,
    )
    symbol_props_cache.get.return_value = aapl_props

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
        symbol_properties=symbol_props_cache,
    )

    broker.submit_market_order.return_value = "BROKER-002"

    internal_id = "TEST-002"
    original_qty = Decimal("50")
    engine.submit_market_order(
        internal_order_id=internal_id,
        symbol="AAPL",
        quantity=original_qty,
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # Both should have the same value
    metadata_qty = engine._order_metadata[internal_id]["quantity"]
    broker_qty = broker.submit_market_order.call_args.kwargs["quantity"]

    assert metadata_qty == broker_qty, f"Metadata={metadata_qty}, Broker={broker_qty}"


def test_no_symbol_properties_doesnt_crash(tmp_path):
    """PATCH 10: Engine works without symbol_properties (no rounding)."""
    from core.execution.engine import OrderExecutionEngine
    from core.brokers.alpaca_connector import BrokerOrderSide
    from core.state import OrderStateMachine, PositionStore

    broker = MagicMock()
    event_bus = MagicMock()
    transaction_log = MagicMock()
    state_machine = OrderStateMachine(event_bus=event_bus, transaction_log=transaction_log)
    position_store = PositionStore(db_path=str(tmp_path / "positions.db"))

    # No symbol_properties provided
    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
        symbol_properties=None,
    )

    broker.submit_market_order.return_value = "BROKER-003"

    internal_id = "TEST-003"
    original_qty = Decimal("123")
    engine.submit_market_order(
        internal_order_id=internal_id,
        symbol="XYZ",
        quantity=original_qty,
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # Should use original quantity (no rounding)
    metadata_qty = engine._order_metadata[internal_id]["quantity"]
    broker_qty = broker.submit_market_order.call_args.kwargs["quantity"]

    assert metadata_qty == original_qty
    assert broker_qty == original_qty
