"""
Order dataclass for complete order representation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict
from core.state.order_machine import OrderStatus


# Order dataclass
@dataclass
class Order:
    """
    Complete order representation with state tracking.
    
    LIFECYCLE:
    1. Created with PENDING state
    2. Submitted → broker_order_id assigned, state = SUBMITTED
    3. Filled → filled_qty/price updated, state = FILLED
    4. Or: Cancelled/Rejected/Expired
    
    ALL FIELDS TRACKED:
    - Order identification (order_id, broker_order_id, symbol)
    - Quantities (quantity, filled_qty, remaining_qty)
    - Prices (entry_price, filled_price)
    - State (state, created_at, submitted_at, filled_at)
    - Strategy (strategy, stop_loss, take_profit)
    - Execution (commission, side, order_type)
    """
    # Identification
    order_id: str
    symbol: str
    strategy: str
    
    # Order parameters
    quantity: Decimal
    side: str  # LONG or SHORT
    order_type: str  # MARKET, LIMIT, etc.
    entry_price: Optional[Decimal] = None
    
    # Risk parameters
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    
    # State tracking
    state: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    
    # Execution tracking
    filled_qty: Decimal = Decimal("0")
    filled_price: Optional[Decimal] = None
    remaining_qty: Optional[Decimal] = None
    commission: Decimal = Decimal("0")
    
    # Timestamps (CRITICAL: Created by caller with clock.now(), not auto-generated)
    created_at: datetime  # Required - no default, prevents backtest time bugs
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    
    # Rejection tracking
    rejection_reason: Optional[str] = None
    
    def __post_init__(self):
        """Calculate remaining_qty if not set."""
        if self.remaining_qty is None:
            self.remaining_qty = self.quantity - self.filled_qty
    
    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.state == OrderStatus.FILLED
    
    @property
    def is_pending(self) -> bool:
        """Check if order is pending submission."""
        return self.state == OrderStatus.PENDING
    
    @property
    def is_active(self) -> bool:
        """Check if order is active (not terminal)."""
        return self.state not in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED
        }
    
    @property
    def fill_percentage(self) -> Decimal:
        """Calculate percentage filled."""
        if self.quantity == 0:
            return Decimal("0")
        return (self.filled_qty / self.quantity) * Decimal("100")
    
    @property
    def total_cost(self) -> Optional[Decimal]:
        """Calculate total execution cost (filled_qty * filled_price + commission)."""
        if self.filled_price is None or self.filled_qty == 0:
            return None
        return (self.filled_qty * self.filled_price) + self.commission
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'strategy': self.strategy,
            'quantity': str(self.quantity),
            'side': self.side,
            'order_type': self.order_type,
            'entry_price': str(self.entry_price) if self.entry_price else None,
            'stop_loss': str(self.stop_loss) if self.stop_loss else None,
            'take_profit': str(self.take_profit) if self.take_profit else None,
            'state': self.state.value,
            'broker_order_id': self.broker_order_id,
            'filled_qty': str(self.filled_qty),
            'filled_price': str(self.filled_price) if self.filled_price else None,
            'remaining_qty': str(self.remaining_qty) if self.remaining_qty else None,
            'commission': str(self.commission),
            'created_at': self.created_at.isoformat(),
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'filled_at': self.filled_at.isoformat() if self.filled_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'rejection_reason': self.rejection_reason,
            'fill_percentage': str(self.fill_percentage)
        }


