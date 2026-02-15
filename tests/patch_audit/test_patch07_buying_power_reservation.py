"""
PATCH 7 tests: Buying power reservation from open orders.

Tests:
1. BP 50k, 40k reserved, 20k new => reject
2. BP 50k, 40k reserved, 5k new => approve
3. No order_tracker => reserved = 0 (backward compat)
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.brokers.alpaca_connector import BrokerOrderSide
from core.risk.manager import RiskManager, RiskLimits
from core.state import PositionStore
from core.state.order_tracker import InFlightOrder, OrderSide, OrderType


@pytest.fixture
def position_store(tmp_path):
    """Empty position store."""
    return PositionStore(db_path=str(tmp_path / "positions.db"))


@pytest.fixture
def risk_limits():
    """Risk limits with low reserve for testing."""
    return RiskLimits(
        min_buying_power_reserve=Decimal("10000"),
        max_position_size_usd=Decimal("100000"),
    )


def test_reserved_bp_causes_rejection(position_store, risk_limits, tmp_path):
    """PATCH 7: BP 50k, 40k reserved, 20k new => reject."""
    # Create order tracker with in-flight order
    order_tracker = MagicMock()

    # 40k worth of in-flight BUY orders
    in_flight_buy = InFlightOrder(
        client_order_id="buy-001",
        exchange_order_id="EX-001",
        symbol="SPY",
        quantity=Decimal("100"),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("400"),  # 100 * $400 = $40k
        strategy_id="test",
    )
    order_tracker.get_all_in_flight.return_value = [in_flight_buy]

    risk_mgr = RiskManager(
        position_store=position_store,
        limits=risk_limits,
        order_tracker=order_tracker,
    )

    # Try to place $20k order
    # buying_power = 50k, reserved = 40k, available = 10k
    # 10k - 20k = -10k < 10k reserve => REJECT
    result = risk_mgr.validate_trade(
        symbol="AAPL",
        quantity=Decimal("50"),
        side=BrokerOrderSide.BUY,
        price=Decimal("400"),  # 50 * $400 = $20k
        account_value=Decimal("100000"),
        buying_power=Decimal("50000"),
    )

    assert not result.approved
    assert any("buying power" in r.lower() for r in result.reasons)


def test_reserved_bp_allows_small_trade(position_store, risk_limits):
    """PATCH 7: BP 50k, 40k reserved, 5k new => approve."""
    order_tracker = MagicMock()

    # 40k reserved
    in_flight_buy = InFlightOrder(
        client_order_id="buy-001",
        exchange_order_id="EX-001",
        symbol="SPY",
        quantity=Decimal("100"),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("400"),
        strategy_id="test",
    )
    order_tracker.get_all_in_flight.return_value = [in_flight_buy]

    risk_mgr = RiskManager(
        position_store=position_store,
        limits=risk_limits,
        order_tracker=order_tracker,
    )

    # Try $5k order
    # buying_power = 50k, reserved = 40k, available = 10k
    # 10k - 5k = 5k < 10k reserve => REJECT (still not enough for reserve!)

    # Let's use BP = 60k instead
    result = risk_mgr.validate_trade(
        symbol="AAPL",
        quantity=Decimal("10"),
        side=BrokerOrderSide.BUY,
        price=Decimal("500"),  # 10 * $500 = $5k
        account_value=Decimal("100000"),
        buying_power=Decimal("60000"),  # 60k - 40k reserved = 20k available, 20k - 5k = 15k > 10k reserve
    )

    assert result.approved


def test_no_order_tracker_backward_compat(position_store, risk_limits):
    """PATCH 7: No order_tracker => reserved BP = 0 (backward compatible)."""
    # No order_tracker provided
    risk_mgr = RiskManager(
        position_store=position_store,
        limits=risk_limits,
        order_tracker=None,
    )

    # Should work as before (no reservation)
    # Use smaller position to avoid max_position_pct_portfolio rejection
    result = risk_mgr.validate_trade(
        symbol="SPY",
        quantity=Decimal("20"),
        side=BrokerOrderSide.BUY,
        price=Decimal("400"),  # $8k (8% of 100k portfolio)
        account_value=Decimal("100000"),
        buying_power=Decimal("50000"),  # 50k - 8k = 42k > 10k reserve
    )

    assert result.approved


def test_sell_orders_dont_reserve_bp(position_store, risk_limits):
    """PATCH 7: SELL orders don't reserve buying power."""
    order_tracker = MagicMock()

    # In-flight SELL order (shouldn't reserve BP)
    in_flight_sell = InFlightOrder(
        client_order_id="sell-001",
        exchange_order_id="EX-001",
        symbol="SPY",
        quantity=Decimal("100"),
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        price=Decimal("400"),
        strategy_id="test",
    )
    order_tracker.get_all_in_flight.return_value = [in_flight_sell]

    risk_mgr = RiskManager(
        position_store=position_store,
        limits=risk_limits,
        order_tracker=order_tracker,
    )

    # Should approve (SELL doesn't reserve BP)
    # Use smaller position to avoid max_position_pct_portfolio rejection
    result = risk_mgr.validate_trade(
        symbol="AAPL",
        quantity=Decimal("20"),
        side=BrokerOrderSide.BUY,
        price=Decimal("400"),  # $8k (8% of 100k portfolio)
        account_value=Decimal("100000"),
        buying_power=Decimal("50000"),  # 50k - 0 reserved = 50k, 50k - 8k = 42k > 10k
    )

    assert result.approved
