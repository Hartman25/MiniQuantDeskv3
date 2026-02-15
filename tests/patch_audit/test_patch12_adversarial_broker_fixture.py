"""
PATCH 12 tests: Adversarial broker test fixture.

Problem: Tests use well-behaved mocks that don't expose edge cases like
rate limits, partial fills, delayed fills, or order rejections.

Solution: Create AdversarialBrokerFixture that simulates real broker behaviors.

Tests:
1. Rate limit simulation (429 errors)
2. Partial fill simulation
3. Order rejection simulation
"""

from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from core.brokers.alpaca_connector import BrokerOrderSide


def test_adversarial_broker_rate_limit():
    """PATCH 12: AdversarialBrokerFixture simulates rate limits."""
    from tests.fixtures.adversarial_broker import AdversarialBrokerFixture

    fixture = AdversarialBrokerFixture(
        rate_limit_probability=1.0,  # Always rate limit on first call
    )

    # First call should trigger rate limit
    with pytest.raises(Exception, match="rate limit|429"):
        fixture.submit_market_order(
            symbol="SPY",
            quantity=Decimal("100"),
            side=BrokerOrderSide.BUY,
        )


def test_adversarial_broker_partial_fills():
    """PATCH 12: AdversarialBrokerFixture simulates partial fills."""
    from tests.fixtures.adversarial_broker import AdversarialBrokerFixture

    fixture = AdversarialBrokerFixture(
        partial_fill_probability=1.0,  # Always partial fill
    )

    order_id = fixture.submit_market_order(
        symbol="AAPL",
        quantity=Decimal("100"),
        side=BrokerOrderSide.BUY,
    )

    # Get order status - should be partially filled
    status, fill_info = fixture.get_order_status(order_id)

    # Should be partial fill
    assert fill_info["filled_qty"] < Decimal("100")
    assert fill_info["filled_qty"] > Decimal("0")


def test_adversarial_broker_rejections():
    """PATCH 12: AdversarialBrokerFixture simulates order rejections."""
    from tests.fixtures.adversarial_broker import AdversarialBrokerFixture

    fixture = AdversarialBrokerFixture(
        rejection_probability=1.0,  # Always reject
    )

    # Should raise rejection error
    with pytest.raises(Exception, match="reject"):
        fixture.submit_market_order(
            symbol="TSLA",
            quantity=Decimal("50"),
            side=BrokerOrderSide.SELL,
        )


def test_adversarial_broker_normal_mode():
    """PATCH 12: AdversarialBrokerFixture works in normal mode (no failures)."""
    from tests.fixtures.adversarial_broker import AdversarialBrokerFixture
    from core.state import OrderStatus

    fixture = AdversarialBrokerFixture(
        rate_limit_probability=0.0,
        partial_fill_probability=0.0,
        rejection_probability=0.0,
    )

    order_id = fixture.submit_market_order(
        symbol="SPY",
        quantity=Decimal("100"),
        side=BrokerOrderSide.BUY,
    )

    assert order_id is not None

    # Should be fully filled
    status, fill_info = fixture.get_order_status(order_id)
    assert status == OrderStatus.FILLED
    assert fill_info["filled_qty"] == Decimal("100")
