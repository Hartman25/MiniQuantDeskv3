"""
PATCH 3 tests: Partial fill handling.

Tests:
1. Order for 100 fills in two increments (50 then 50) => position = 100
2. Partial fill updates position incrementally
3. Position uses filled_qty, not order quantity
"""

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.brokers.alpaca_connector import BrokerOrderSide
from core.execution.engine import OrderExecutionEngine
from core.state import OrderStateMachine, OrderStatus, PositionStore


@pytest.fixture
def mock_components(tmp_path):
    """Create mocked components for engine testing."""
    broker = MagicMock()
    event_bus = MagicMock()
    transaction_log = MagicMock()

    state_machine = OrderStateMachine(event_bus=event_bus, transaction_log=transaction_log)
    position_store = PositionStore(db_path=str(tmp_path / "positions.db"))

    # Mock broker submit returns
    broker.submit_market_order.return_value = "BROKER-001"
    broker.get_order_status.return_value = (OrderStatus.FILLED, {
        "filled_qty": Decimal("100"),
        "filled_avg_price": Decimal("100.00"),
    })

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
    )

    return broker, state_machine, position_store, engine


def test_partial_fill_incremental_position_update(mock_components):
    """PATCH 3: Order for 100 fills 50 then 50 => position = 100."""
    broker, state_machine, position_store, engine = mock_components

    internal_id = "TEST-001"
    symbol = "SPY"

    # Submit order for 100
    broker_id = engine.submit_market_order(
        internal_order_id=internal_id,
        symbol=symbol,
        quantity=Decimal("100"),
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # First partial fill: 50 shares at $100
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.PARTIALLY_FILLED,
        fill_info={
            "filled_qty": Decimal("50"),
            "filled_avg_price": Decimal("100.00"),
        },
    )

    # Check position: should be 50
    pos = position_store.get(symbol)
    assert pos is not None
    assert pos.quantity == Decimal("50")
    assert pos.entry_price == Decimal("100.00")

    # Second partial fill: cumulative 100 shares at $100
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.PARTIALLY_FILLED,
        to_state=OrderStatus.FILLED,
        fill_info={
            "filled_qty": Decimal("100"),  # cumulative
            "filled_avg_price": Decimal("100.00"),
        },
    )

    # Check position: should be 100 now
    pos = position_store.get(symbol)
    assert pos is not None
    assert pos.quantity == Decimal("100")
    assert pos.entry_price == Decimal("100.00")


def test_partial_fill_with_different_prices(mock_components):
    """PATCH 3: Partial fills with different prices average correctly."""
    broker, state_machine, position_store, engine = mock_components

    internal_id = "TEST-002"
    symbol = "AAPL"

    broker_id = engine.submit_market_order(
        internal_order_id=internal_id,
        symbol=symbol,
        quantity=Decimal("100"),
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # First partial: 60 shares at $150
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.PARTIALLY_FILLED,
        fill_info={
            "filled_qty": Decimal("60"),
            "filled_avg_price": Decimal("150.00"),
        },
    )

    pos = position_store.get(symbol)
    assert pos.quantity == Decimal("60")
    assert pos.entry_price == Decimal("150.00")

    # Second partial: cumulative 100 at avg $151 (60@150 + 40@153)
    # Engine uses incremental qty (40) at current avg price (151)
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.PARTIALLY_FILLED,
        to_state=OrderStatus.FILLED,
        fill_info={
            "filled_qty": Decimal("100"),
            "filled_avg_price": Decimal("151.00"),
        },
    )

    pos = position_store.get(symbol)
    assert pos.quantity == Decimal("100")
    # Average price: (60 * 150 + 40 * 151) / 100 = 150.40
    expected_avg = (Decimal("60") * Decimal("150") + Decimal("40") * Decimal("151")) / Decimal("100")
    assert pos.entry_price == expected_avg


def test_cumulative_tracker_cleanup_on_final_fill(tmp_path):
    """PATCH 3: Cumulative filled_qty tracker is cleaned up on final fill."""
    broker = MagicMock()
    event_bus = MagicMock()
    transaction_log = MagicMock()

    state_machine = OrderStateMachine(event_bus=event_bus, transaction_log=transaction_log)
    position_store = PositionStore(db_path=str(tmp_path / "positions.db"))

    broker.submit_market_order.return_value = "BROKER-003"

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
    )

    internal_id = "TEST-003"
    symbol = "TSLA"

    broker_id = engine.submit_market_order(
        internal_order_id=internal_id,
        symbol=symbol,
        quantity=Decimal("50"),
        side=BrokerOrderSide.BUY,
        strategy="test",
    )

    # First partial fill: 25 @ $200
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.PARTIALLY_FILLED,
        fill_info={
            "filled_qty": Decimal("25"),
            "filled_avg_price": Decimal("200.00"),
        },
    )

    pos = position_store.get(symbol)
    assert pos.quantity == Decimal("25")

    # Verify cumulative tracker
    assert engine._cumulative_filled_qty[internal_id] == Decimal("25")

    # Final fill: cumulative 50 (incremental 25)
    engine._handle_status_change(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        from_state=OrderStatus.PARTIALLY_FILLED,
        to_state=OrderStatus.FILLED,
        fill_info={
            "filled_qty": Decimal("50"),
            "filled_avg_price": Decimal("200.00"),
        },
    )

    pos = position_store.get(symbol)
    assert pos.quantity == Decimal("50")

    # Cumulative tracker cleaned up on final fill
    assert internal_id not in engine._cumulative_filled_qty
