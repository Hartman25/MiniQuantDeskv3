"""
PATCH 12: Adversarial broker test fixture.

Simulates real broker edge cases:
- Rate limits (429 errors)
- Partial fills
- Order rejections
- Delayed fills
- Network errors

Pattern stolen from: Chaos engineering / fault injection testing
"""

from decimal import Decimal
from typing import Dict, Optional, Tuple
import random
import uuid

from core.state import OrderStatus
from core.brokers.alpaca_connector import BrokerOrderSide


class BrokerRateLimitError(Exception):
    """Simulated rate limit error."""
    pass


class BrokerRejectionError(Exception):
    """Simulated order rejection error."""
    pass


class AdversarialBrokerFixture:
    """
    Test fixture that simulates adversarial broker behaviors.

    Configurable failure probabilities allow testing edge cases:
    - Rate limits
    - Partial fills
    - Order rejections
    - Network errors

    Usage:
        # Always trigger rate limits
        broker = AdversarialBrokerFixture(rate_limit_probability=1.0)

        # 30% chance of partial fills
        broker = AdversarialBrokerFixture(partial_fill_probability=0.3)

        # Normal mode (no failures)
        broker = AdversarialBrokerFixture()
    """

    def __init__(
        self,
        rate_limit_probability: float = 0.0,
        partial_fill_probability: float = 0.0,
        rejection_probability: float = 0.0,
        network_error_probability: float = 0.0,
    ):
        """
        Initialize adversarial broker.

        Args:
            rate_limit_probability: Probability of rate limit (0.0-1.0)
            partial_fill_probability: Probability of partial fill (0.0-1.0)
            rejection_probability: Probability of order rejection (0.0-1.0)
            network_error_probability: Probability of network error (0.0-1.0)
        """
        self.rate_limit_probability = rate_limit_probability
        self.partial_fill_probability = partial_fill_probability
        self.rejection_probability = rejection_probability
        self.network_error_probability = network_error_probability

        # Track submitted orders
        self._orders: Dict[str, Dict] = {}

    def _should_trigger(self, probability: float) -> bool:
        """Check if event should trigger based on probability."""
        return random.random() < probability

    def submit_market_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
    ) -> str:
        """
        Submit market order with possible failures.

        Args:
            symbol: Symbol to trade
            quantity: Order quantity
            side: BUY or SELL

        Returns:
            Order ID

        Raises:
            BrokerRateLimitError: If rate limit triggered
            BrokerRejectionError: If rejection triggered
            ConnectionError: If network error triggered
        """
        # Network error check
        if self._should_trigger(self.network_error_probability):
            raise ConnectionError("Simulated network error")

        # Rate limit check
        if self._should_trigger(self.rate_limit_probability):
            raise BrokerRateLimitError("Rate limit exceeded (429)")

        # Rejection check
        if self._should_trigger(self.rejection_probability):
            raise BrokerRejectionError(f"Order rejected for {symbol}")

        # Create order
        order_id = f"ADV-{uuid.uuid4().hex[:8]}"

        # Determine fill status
        if self._should_trigger(self.partial_fill_probability):
            # Partial fill: 40-90% filled
            fill_pct = random.uniform(0.4, 0.9)
            filled_qty = quantity * Decimal(str(fill_pct))
            filled_qty = filled_qty.quantize(Decimal("1"))  # Round to whole shares
            status = OrderStatus.PARTIALLY_FILLED
        else:
            # Full fill
            filled_qty = quantity
            status = OrderStatus.FILLED

        # Store order
        self._orders[order_id] = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "status": status,
            "filled_qty": filled_qty,
            "filled_avg_price": Decimal("100.00"),  # Mock price
        }

        return order_id

    def submit_limit_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        limit_price: Decimal,
    ) -> str:
        """
        Submit limit order with possible failures.

        Args:
            symbol: Symbol to trade
            quantity: Order quantity
            side: BUY or SELL
            limit_price: Limit price

        Returns:
            Order ID
        """
        # Reuse market order logic for now
        return self.submit_market_order(symbol, quantity, side)

    def get_order_status(self, order_id: str) -> Tuple[OrderStatus, Dict]:
        """
        Get order status.

        Args:
            order_id: Order ID

        Returns:
            (status, fill_info) tuple
        """
        if order_id not in self._orders:
            raise ValueError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        return order["status"], {
            "filled_qty": order["filled_qty"],
            "filled_avg_price": order["filled_avg_price"],
        }

    def get_account_info(self) -> Dict:
        """
        Get account info.

        Returns:
            Mock account info
        """
        if self._should_trigger(self.rate_limit_probability):
            raise BrokerRateLimitError("Rate limit exceeded (429)")

        return {
            "buying_power": Decimal("100000"),
            "cash": Decimal("50000"),
            "portfolio_value": Decimal("150000"),
        }

    def get_positions(self) -> list:
        """
        Get positions.

        Returns:
            Empty list (mock)
        """
        if self._should_trigger(self.rate_limit_probability):
            raise BrokerRateLimitError("Rate limit exceeded (429)")

        return []

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order.

        Args:
            order_id: Order ID

        Returns:
            True if cancelled
        """
        if order_id in self._orders:
            self._orders[order_id]["status"] = OrderStatus.CANCELLED
            return True
        return False
