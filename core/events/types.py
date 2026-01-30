"""
Event type definitions - FIXED VERSION with proper dataclass structure.

All events are self-contained dataclasses with timestamp last.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from decimal import Decimal


# Helper for default timestamp
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# ORDER LIFECYCLE EVENTS
# ============================================================================

@dataclass(frozen=True)
class OrderCreatedEvent:
    """Emitted when order is created locally."""
    order_id: str
    symbol: str
    quantity: Decimal
    side: str
    order_type: str
    strategy: str
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class OrderSubmittedEvent:
    """Emitted when order is successfully submitted to broker."""
    order_id: str
    broker_order_id: str
    symbol: str
    quantity: Decimal
    side: str
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class OrderPartiallyFilledEvent:
    """Emitted when order is partially executed."""
    order_id: str
    broker_order_id: str
    symbol: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class OrderFilledEvent:
    """Emitted when order is fully executed."""
    order_id: str
    broker_order_id: str
    symbol: str
    filled_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    timestamp: datetime = field(default_factory=now_utc)
    
    @property
    def total_cost(self) -> Decimal:
        return (self.filled_quantity * self.fill_price) + self.commission


@dataclass(frozen=True)
class OrderCancelledEvent:
    """Emitted when order is cancelled."""
    order_id: str
    broker_order_id: str
    symbol: str
    reason: str
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class OrderRejectedEvent:
    """Emitted when order is rejected by broker or risk gate."""
    order_id: str
    symbol: str
    reason: str
    rejected_by: str
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class OrderExpiredEvent:
    """Emitted when order expires without execution."""
    order_id: str
    broker_order_id: Optional[str]
    symbol: str
    reason: str
    timestamp: datetime = field(default_factory=now_utc)


# ============================================================================
# POSITION EVENTS
# ============================================================================

@dataclass(frozen=True)
class PositionOpenedEvent:
    """Emitted when new position is opened."""
    position_id: str
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    side: str
    strategy: str
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class PositionClosedEvent:
    """Emitted when position is fully closed."""
    position_id: str
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    commission: Decimal
    hold_duration_seconds: float
    closed_reason: str
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class PositionModifiedEvent:
    """Emitted when position stop/target is modified."""
    position_id: str
    symbol: str
    old_stop_loss: Optional[Decimal]
    new_stop_loss: Optional[Decimal]
    old_take_profit: Optional[Decimal]
    new_take_profit: Optional[Decimal]
    modified_by: str
    timestamp: datetime = field(default_factory=now_utc)


# ============================================================================
# RISK EVENTS
# ============================================================================

@dataclass(frozen=True)
class RiskLimitBreachedEvent:
    """Emitted when risk limit is breached."""
    limit_type: str
    current_value: Decimal
    limit_value: Decimal
    symbol: Optional[str] = None
    action_taken: str = "order_blocked"
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class KillSwitchActivatedEvent:
    """Emitted when emergency kill switch is triggered."""
    reason: str
    trigger_source: str
    all_positions_closed: bool
    all_orders_cancelled: bool
    timestamp: datetime = field(default_factory=now_utc)


# ============================================================================
# SYSTEM EVENTS
# ============================================================================

@dataclass(frozen=True)
class HeartbeatEvent:
    """Regular system health ping."""
    system_status: str
    active_positions: int
    open_orders: int
    cash_available: Decimal
    total_equity: Decimal
    unrealized_pnl: Decimal
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class StrategyStartedEvent:
    """Emitted when strategy begins execution."""
    strategy_name: str
    strategy_id: str
    config: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class StrategyStoppedEvent:
    """Emitted when strategy stops execution."""
    strategy_name: str
    strategy_id: str
    reason: str
    final_pnl: Optional[Decimal] = None
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class MarketDataReceivedEvent:
    """Emitted when new market data arrives."""
    symbol: str
    provider: str
    data_timestamp: datetime
    latency_ms: float
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class ReconciliationCompletedEvent:
    """Emitted after broker reconciliation."""
    positions_synced: int
    orders_synced: int
    discrepancies_found: int
    discrepancies: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=now_utc)


@dataclass(frozen=True)
class ErrorEvent:
    """Emitted on system errors."""
    error_type: str
    error_message: str
    component: str
    severity: str
    stack_trace: Optional[str] = None
    recoverable: bool = True
    timestamp: datetime = field(default_factory=now_utc)


# ============================================================================
# EVENT FACTORY
# ============================================================================

class EventFactory:
    """Factory for creating events from broker/API responses."""
    
    @staticmethod
    def from_alpaca_order_update(alpaca_order: Any, local_order_id: str):
        """Convert Alpaca order update to appropriate event type."""
        status = getattr(alpaca_order, 'status', None)
        
        if status == 'new':
            return OrderSubmittedEvent(
                order_id=local_order_id,
                broker_order_id=str(alpaca_order.id),
                symbol=alpaca_order.symbol,
                quantity=Decimal(str(alpaca_order.qty)),
                side=alpaca_order.side.upper()
            )
        
        elif status == 'partially_filled':
            filled_qty = Decimal(str(alpaca_order.filled_qty))
            remaining_qty = Decimal(str(alpaca_order.qty)) - filled_qty
            return OrderPartiallyFilledEvent(
                order_id=local_order_id,
                broker_order_id=str(alpaca_order.id),
                symbol=alpaca_order.symbol,
                filled_quantity=filled_qty,
                remaining_quantity=remaining_qty,
                fill_price=Decimal(str(alpaca_order.filled_avg_price or 0)),
                commission=Decimal('0')
            )
        
        elif status == 'filled':
            return OrderFilledEvent(
                order_id=local_order_id,
                broker_order_id=str(alpaca_order.id),
                symbol=alpaca_order.symbol,
                filled_quantity=Decimal(str(alpaca_order.filled_qty)),
                fill_price=Decimal(str(alpaca_order.filled_avg_price)),
                commission=Decimal('0')
            )
        
        elif status == 'canceled':
            return OrderCancelledEvent(
                order_id=local_order_id,
                broker_order_id=str(alpaca_order.id),
                symbol=alpaca_order.symbol,
                reason=getattr(alpaca_order, 'cancel_reason', 'user_request')
            )
        
        elif status in ['rejected', 'pending_cancel', 'stopped']:
            return OrderRejectedEvent(
                order_id=local_order_id,
                symbol=alpaca_order.symbol,
                reason=status,
                rejected_by='broker'
            )
        
        elif status == 'expired':
            return OrderExpiredEvent(
                order_id=local_order_id,
                broker_order_id=str(alpaca_order.id),
                symbol=alpaca_order.symbol,
                reason='time_in_force_expired'
            )
        
        else:
            return ErrorEvent(
                error_type='unknown_order_status',
                error_message=f"Unknown Alpaca order status: {status}",
                component='EventFactory',
                severity='medium',
                recoverable=True
            )
