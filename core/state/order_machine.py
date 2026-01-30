"""
Order State Machine with Order object storage and transition validation.

ARCHITECTURE:
- Stores Order objects in memory
- Validates all state transitions
- Updates orders on transitions
- Emits events for subscribers
- Logs to transaction log
- Thread-safe via lock

CRITICAL RULES:
1. All transitions must be pre-defined as valid
2. Invalid transitions raise OrderStateMachineError
3. Broker confirmation required for broker-originated transitions
4. All transitions emit events and log to TransactionLog
5. Terminal states cannot transition further
6. Thread-safe via explicit locking
7. Orders stored and retrievable for reconciliation

Based on LEAN's OrderEvent pattern with enhanced safety + storage.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, Tuple, List
from datetime import datetime, timezone
from decimal import Decimal
import threading

from core.logging import get_logger, LogStream, LogContext


# ============================================================================
# ORDER STATUS ENUM
# ============================================================================

class OrderStatus(Enum):
    """Canonical order states."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ============================================================================
# TRANSITION DEFINITION
# ============================================================================

@dataclass(frozen=True)
class OrderTransition:
    """Immutable definition of a valid state transition."""
    from_state: OrderStatus
    to_state: OrderStatus
    requires_broker_confirmation: bool
    description: str = ""


# ============================================================================
# VALID TRANSITIONS REGISTRY
# ============================================================================

VALID_TRANSITIONS: Set[OrderTransition] = {
    OrderTransition(OrderStatus.PENDING, OrderStatus.SUBMITTED, True, "Broker acknowledged"),
    OrderTransition(OrderStatus.SUBMITTED, OrderStatus.FILLED, True, "Fully executed"),
    OrderTransition(OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED, True, "Partial execution"),
    OrderTransition(OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, True, "Remaining filled"),
    OrderTransition(OrderStatus.SUBMITTED, OrderStatus.CANCELLED, True, "Cancelled at broker"),
    OrderTransition(OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED, True, "Remaining cancelled"),
    OrderTransition(OrderStatus.SUBMITTED, OrderStatus.REJECTED, True, "Broker rejected"),
    OrderTransition(OrderStatus.PENDING, OrderStatus.REJECTED, False, "Risk gate rejected"),
    OrderTransition(OrderStatus.SUBMITTED, OrderStatus.EXPIRED, False, "Expired"),
}


# ============================================================================
# STATE CHANGE EVENT
# ============================================================================

@dataclass
class OrderStateChangedEvent:
    """Event emitted on every state transition."""
    order_id: str
    from_state: OrderStatus
    to_state: OrderStatus
    timestamp: datetime
    broker_order_id: Optional[str] = None
    filled_qty: Optional[Decimal] = None
    remaining_qty: Optional[Decimal] = None
    fill_price: Optional[Decimal] = None
    reason: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "event_type": "OrderStateChanged",
            "order_id": self.order_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "timestamp": self.timestamp.isoformat(),
            "broker_order_id": self.broker_order_id,
            "filled_qty": str(self.filled_qty) if self.filled_qty else None,
            "remaining_qty": str(self.remaining_qty) if self.remaining_qty else None,
            "fill_price": str(self.fill_price) if self.fill_price else None,
            "reason": self.reason,
            "metadata": self.metadata
        }


# ============================================================================
# EXCEPTIONS
# ============================================================================

class OrderStateMachineError(Exception):
    """Base exception for order state machine errors."""
    pass

class InvalidTransitionError(OrderStateMachineError):
    """Invalid state transition attempted."""
    pass

class BrokerConfirmationRequiredError(OrderStateMachineError):
    """Broker confirmation missing for transition."""
    pass

class TerminalStateError(OrderStateMachineError):
    """Cannot transition from terminal state."""
    pass


# ============================================================================
# ORDER DATACLASS
# ============================================================================

@dataclass
class Order:
    """Complete order representation with state tracking."""
    # Identification
    order_id: str
    symbol: str
    strategy: str
    
    # Order parameters
    quantity: Decimal
    side: str
    order_type: str
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
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # PATCH 4
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    
    # Rejection tracking
    rejection_reason: Optional[str] = None
    
    def __post_init__(self):
        if self.remaining_qty is None:
            self.remaining_qty = self.quantity - self.filled_qty
    
    @property
    def is_filled(self) -> bool:
        return self.state == OrderStatus.FILLED
    
    @property
    def is_pending(self) -> bool:
        return self.state == OrderStatus.PENDING
    
    @property
    def is_active(self) -> bool:
        return self.state not in {
            OrderStatus.FILLED, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED
        }
    
    @property
    def fill_percentage(self) -> Decimal:
        if self.quantity == 0:
            return Decimal("0")
        return (self.filled_qty / self.quantity) * Decimal("100")
    
    @property
    def total_cost(self) -> Optional[Decimal]:
        if self.filled_price is None or self.filled_qty == 0:
            return None
        return (self.filled_qty * self.filled_price) + self.commission
    
    def to_dict(self) -> Dict:
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'state': self.state.value,
            'quantity': str(self.quantity),
            'filled_qty': str(self.filled_qty),
            'broker_order_id': self.broker_order_id
        }


# ============================================================================
# ORDER STATE MACHINE
# ============================================================================

class OrderStateMachine:
    """
    Thread-safe order state machine with Order object storage.
    
    KEY FEATURES:
    - Stores Order objects (_orders dict)
    - Validates transitions before execution
    - Updates stored orders on transitions
    - Emits events for subscribers
    - Thread-safe via lock
    
    USAGE:
        machine = OrderStateMachine(event_bus, transaction_log)
        
        # Create order
        order = machine.create_order(
            order_id="ORD_001",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Transition to SUBMITTED
        machine.transition(
            order_id="ORD_001",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK_123"
        )
        
        # Retrieve order
        order = machine.get_order("ORD_001")
        assert order.state == OrderStatus.SUBMITTED
    """
    
    def __init__(self, event_bus, transaction_log):
        """Initialize state machine with order storage."""
        self.event_bus = event_bus
        self.transaction_log = transaction_log
        self.logger = get_logger(LogStream.ORDERS)
        
        # Build transition lookup map
        self._transition_map: Dict[Tuple[OrderStatus, OrderStatus], OrderTransition] = {
            (t.from_state, t.to_state): t for t in VALID_TRANSITIONS
        }
        
        # Terminal states
        self._terminal_states = {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED
        }
        
        # ORDER STORAGE
        self._orders: Dict[str, Order] = {}
        
        # Thread safety
        self._lock = threading.Lock()
        
        self.logger.info("OrderStateMachine initialized with order storage")
    
    # ========================================================================
    # ORDER CREATION AND RETRIEVAL
    # ========================================================================
    
    def create_order(
        self,
        order_id: str,
        symbol: str,
        quantity: Decimal,
        side: str,
        order_type: str,
        strategy: str,
        entry_price: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None
    ) -> Order:
        """Create new order with PENDING state."""
        with self._lock:
            if order_id in self._orders:
                raise OrderStateMachineError(f"Order {order_id} already exists")
            
            order = Order(
                order_id=order_id,
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                strategy=strategy,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                state=OrderStatus.PENDING
            )
            
            self._orders[order_id] = order
            
            self.logger.info(f"Order created: {order_id}", extra={
                "order_id": order_id, "symbol": symbol, "quantity": str(quantity)
            })
            
            return order
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Retrieve order by ID."""
        with self._lock:
            return self._orders.get(order_id)
    
    def get_all_orders(self) -> List[Order]:
        """Get all orders."""
        with self._lock:
            return list(self._orders.values())
    
    def get_pending_orders(self) -> List[Order]:
        """Get all non-terminal orders."""
        with self._lock:
            return [o for o in self._orders.values() if o.state not in self._terminal_states]
    
    def get_orders_by_symbol(self, symbol: str) -> List[Order]:
        """Get orders for specific symbol."""
        with self._lock:
            return [o for o in self._orders.values() if o.symbol == symbol]
    
    def get_orders_by_state(self, state: OrderStatus) -> List[Order]:
        """Get orders in specific state."""
        with self._lock:
            return [o for o in self._orders.values() if o.state == state]
    
    # ========================================================================
    # STATE TRANSITIONS
    # ========================================================================
    
    def transition(
        self,
        order_id: str,
        from_state: OrderStatus,
        to_state: OrderStatus,
        broker_order_id: Optional[str] = None,
        filled_qty: Optional[Decimal] = None,
        remaining_qty: Optional[Decimal] = None,
        fill_price: Optional[Decimal] = None,
        reason: Optional[str] = None,
        **metadata
    ) -> OrderStateChangedEvent:
        """
        Execute state transition with validation and order update.
        
        Raises:
            InvalidTransitionError: Transition not allowed
            BrokerConfirmationRequiredError: Missing broker_order_id
            TerminalStateError: Cannot transition from terminal state
        """
        with self._lock:
            with LogContext(order_id):
                # Validate and execute
                event = self._execute_transition(
                    order_id, from_state, to_state,
                    broker_order_id, filled_qty, remaining_qty,
                    fill_price, reason, metadata
                )
                
                # Update stored order
                self._update_order_from_transition(
                    order_id, to_state, broker_order_id,
                    filled_qty, remaining_qty, fill_price, reason
                )
                
                return event
    
    def _execute_transition(
        self, order_id, from_state, to_state,
        broker_order_id, filled_qty, remaining_qty,
        fill_price, reason, metadata
    ) -> OrderStateChangedEvent:
        """Execute transition with validation."""
        
        # Check terminal state
        if from_state in self._terminal_states:
            raise TerminalStateError(f"Cannot transition from {from_state.value}")
        
        # Lookup transition
        transition = self._transition_map.get((from_state, to_state))
        if not transition:
            raise InvalidTransitionError(
                f"Invalid: {from_state.value} → {to_state.value}"
            )
        
        # Check broker confirmation
        if transition.requires_broker_confirmation and not broker_order_id:
            raise BrokerConfirmationRequiredError(
                f"Broker confirmation required for {from_state.value} → {to_state.value}"
            )
        
        # Validate quantities
        if filled_qty is not None and filled_qty <= 0:
            raise OrderStateMachineError(f"filled_qty must be positive: {filled_qty}")
        if fill_price is not None and fill_price <= 0:
            raise OrderStateMachineError(f"fill_price must be positive: {fill_price}")
        
        # Create event
        event = OrderStateChangedEvent(
            order_id=order_id,
            from_state=from_state,
            to_state=to_state,
            timestamp=datetime.now(timezone.utc),  # PATCH 4: UTC-aware
            broker_order_id=broker_order_id,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            fill_price=fill_price,
            reason=reason,
            metadata=metadata
        )
        
        # Log and emit
        self.transaction_log.append(event)
        self.event_bus.emit(event)
        
        self.logger.info(f"Transition: {from_state.value} → {to_state.value}", extra={
            "order_id": order_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "broker_order_id": broker_order_id
        })
        
        return event
    
    def _update_order_from_transition(
        self, order_id, to_state, broker_order_id,
        filled_qty, remaining_qty, fill_price, reason
    ):
        """Update stored order from transition."""
        order = self._orders.get(order_id)
        if not order:
            self.logger.warning(f"Order {order_id} not found for update")
            return
        
        # Update state
        order.state = to_state
        
        # Update broker ID
        if broker_order_id:
            order.broker_order_id = broker_order_id
        
        # Update timestamps
        now = datetime.now(timezone.utc)  # PATCH 4: UTC-aware
        if to_state == OrderStatus.SUBMITTED and not order.submitted_at:
            order.submitted_at = now
        elif to_state == OrderStatus.FILLED and not order.filled_at:
            order.filled_at = now
        elif to_state == OrderStatus.CANCELLED and not order.cancelled_at:
            order.cancelled_at = now
        
        # Update fills
        if filled_qty is not None:
            order.filled_qty = filled_qty
        if remaining_qty is not None:
            order.remaining_qty = remaining_qty
        if fill_price is not None:
            order.filled_price = fill_price
        
        # Update rejection reason
        if to_state == OrderStatus.REJECTED and reason:
            order.rejection_reason = reason
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def is_terminal(self, status: OrderStatus) -> bool:
        """Check if status is terminal."""
        return status in self._terminal_states
    
    def get_valid_transitions(self, from_state: OrderStatus) -> Set[OrderStatus]:
        """Get valid next states."""
        return {to for (f, to) in self._transition_map.keys() if f == from_state}
    
    def validate_transition(
        self,
        from_state: OrderStatus,
        to_state: OrderStatus,
        broker_order_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validate transition without executing."""
        if from_state in self._terminal_states:
            return False, f"{from_state.value} is terminal"
        
        transition = self._transition_map.get((from_state, to_state))
        if not transition:
            return False, f"No transition {from_state.value} → {to_state.value}"
        
        if transition.requires_broker_confirmation and not broker_order_id:
            return False, "Broker confirmation required"
        
        return True, None
