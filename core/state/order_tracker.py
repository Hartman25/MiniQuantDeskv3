"""
Enhanced Order Tracker for lifecycle management and drift detection.

Complements OrderStateMachine with:
- Complete lifecycle tracking
- Fill aggregation
- Orphan order detection (broker has, we don't)
- Shadow order detection (we have, broker doesn't)
- Amendment history

Pattern stolen from: Hummingbot order_tracker.py

This does NOT replace OrderStateMachine - they work together:
- OrderStateMachine: State transitions, validation
- OrderTracker: Lifecycle metadata, drift detection
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import logging
import threading

from core.state.order_machine import OrderStatus

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class FillEvent:
    """Individual fill event"""
    timestamp: datetime
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal('0')
    fill_id: Optional[str] = None
    
    @property
    def gross_amount(self) -> Decimal:
        """Gross fill amount before commission"""
        return self.quantity * self.price
    
    @property
    def net_amount(self) -> Decimal:
        """Net fill amount after commission"""
        return self.gross_amount - self.commission


@dataclass
class InFlightOrder:
    """
    Complete order lifecycle tracking.
    
    Tracks everything about an order from creation to completion.
    Hummingbot calls this "InFlightOrder" - order in progress.
    """
    # Identity
    client_order_id: str  # Our ID
    exchange_order_id: Optional[str] = None  # Broker ID
    
    # Order details
    symbol: str = ""
    quantity: Decimal = Decimal('0')
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    price: Optional[Decimal] = None  # For limit orders
    stop_price: Optional[Decimal] = None  # For stop orders
    
    # Lifecycle timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    first_fill_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_update_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Fill tracking
    filled_quantity: Decimal = Decimal('0')
    average_fill_price: Optional[Decimal] = None
    fills: List[FillEvent] = field(default_factory=list)
    total_commission: Decimal = Decimal('0')
    
    # State
    current_state: OrderStatus = OrderStatus.PENDING
    is_done: bool = False
    is_cancelled: bool = False
    is_failed: bool = False
    
    # Metadata
    strategy_id: Optional[str] = None
    cancel_reason: Optional[str] = None
    reject_reason: Optional[str] = None
    
    @property
    def remaining_quantity(self) -> Decimal:
        """Unfilled quantity"""
        return self.quantity - self.filled_quantity
    
    @property
    def fill_percentage(self) -> float:
        """Percentage filled"""
        if self.quantity == 0:
            return 0.0
        return float(self.filled_quantity / self.quantity) * 100
    
    @property
    def is_partially_filled(self) -> bool:
        """Has partial fill"""
        return self.filled_quantity > 0 and self.filled_quantity < self.quantity
    
    def add_fill(self, fill: FillEvent):
        """Add fill event"""
        self.fills.append(fill)
        self.filled_quantity += fill.quantity
        self.total_commission += fill.commission
        
        if self.first_fill_at is None:
            self.first_fill_at = fill.timestamp
        
        # Recalculate average price
        total_value = sum(f.gross_amount for f in self.fills)
        total_qty = sum(f.quantity for f in self.fills)
        if total_qty > 0:
            self.average_fill_price = total_value / total_qty
        
        self.last_update_at = datetime.now(timezone.utc)
    
    def mark_completed(self, reason: str = "filled"):
        """Mark order as complete"""
        self.is_done = True
        self.completed_at = datetime.now(timezone.utc)
        self.last_update_at = self.completed_at
        
        if reason == "cancelled":
            self.is_cancelled = True
        elif reason in ("rejected", "failed"):
            self.is_failed = True


class OrderTracker:
    """
    Tracks all orders through their complete lifecycle.
    
    Detects:
    - Orphan orders (broker has, we don't know about)
    - Shadow orders (we think exist, broker doesn't have)
    - Fill discrepancies
    - State drift
    
    Usage:
        tracker = OrderTracker()
        
        # Start tracking when order created
        order = InFlightOrder(
            client_order_id="ORD_001",
            symbol="SPY",
            quantity=10,
            side=OrderSide.BUY
        )
        tracker.start_tracking(order)
        
        # Update when filled
        tracker.process_fill("ORD_001", FillEvent(...))
        
        # Check for drift
        orphans = tracker.get_orphaned_orders(broker_orders)
        shadows = tracker.get_shadow_orders(broker_orders)
    """
    
    def __init__(self):
        self._in_flight_orders: Dict[str, InFlightOrder] = {}
        self._completed_orders: Dict[str, InFlightOrder] = {}

        # Tracking sets for quick lookups
        self._exchange_id_to_client_id: Dict[str, str] = {}

        # PATCH 7: Thread-safety lock for mutation methods
        self._lock = threading.Lock()

        logger.info("OrderTracker initialized")
    
    def start_tracking(self, order: InFlightOrder):
        """Begin tracking an order (thread-safe)."""
        with self._lock:
            self._in_flight_orders[order.client_order_id] = order

            if order.exchange_order_id:
                self._exchange_id_to_client_id[order.exchange_order_id] = order.client_order_id

        logger.info(
            f"Started tracking order: {order.client_order_id}",
            extra={
                'client_order_id': order.client_order_id,
                'symbol': order.symbol,
                'quantity': str(order.quantity),
                'side': order.side.value
            }
        )
    
    def stop_tracking(self, client_order_id: str, reason: str = "completed"):
        """
        Stop tracking an order (move to completed).  Thread-safe.

        Args:
            client_order_id: Order ID
            reason: Why stopped (completed, cancelled, rejected)
        """
        with self._lock:
            if client_order_id not in self._in_flight_orders:
                logger.warning(f"Tried to stop tracking unknown order: {client_order_id}")
                return

            order = self._in_flight_orders[client_order_id]
            order.mark_completed(reason)

            # Move to completed
            self._completed_orders[client_order_id] = order
            del self._in_flight_orders[client_order_id]

            # Remove exchange ID mapping
            if order.exchange_order_id in self._exchange_id_to_client_id:
                del self._exchange_id_to_client_id[order.exchange_order_id]

        logger.info(
            f"Stopped tracking order: {client_order_id} ({reason})",
            extra={
                'client_order_id': client_order_id,
                'reason': reason,
                'filled_qty': str(order.filled_quantity),
                'avg_price': str(order.average_fill_price) if order.average_fill_price else None
            }
        )
    
    def process_order_update(self, client_order_id: str, update: dict):
        """
        Process order status update.  Thread-safe.

        Args:
            client_order_id: Order ID
            update: Update dict with status, filled_qty, etc
        """
        should_stop = False
        stop_reason = ""

        with self._lock:
            if client_order_id not in self._in_flight_orders:
                logger.warning(
                    f"Received update for unknown order: {client_order_id}",
                    extra={'update': update}
                )
                return

            order = self._in_flight_orders[client_order_id]

            # Update exchange ID if provided
            if 'exchange_order_id' in update and update['exchange_order_id']:
                order.exchange_order_id = update['exchange_order_id']
                self._exchange_id_to_client_id[update['exchange_order_id']] = client_order_id

            # Update state
            if 'status' in update:
                old_state = order.current_state
                new_state = OrderStatus(update['status'])
                order.current_state = new_state

                if new_state != old_state:
                    logger.info(
                        f"Order state changed: {client_order_id} {old_state.value} -> {new_state.value}",
                        extra={'client_order_id': client_order_id, 'old_state': old_state.value, 'new_state': new_state.value}
                    )

            # Update submitted timestamp
            if 'submitted_at' in update and not order.submitted_at:
                order.submitted_at = update['submitted_at']

            order.last_update_at = datetime.now(timezone.utc)

            # If done, flag for stop_tracking (outside lock to avoid re-entrant lock)
            if update.get('status') in ['FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED']:
                should_stop = True
                stop_reason = update['status'].lower()

        if should_stop:
            self.stop_tracking(client_order_id, stop_reason)
    
    def process_fill(self, client_order_id: str, fill: FillEvent):
        """Process fill event.  Thread-safe."""
        should_stop = False

        with self._lock:
            if client_order_id not in self._in_flight_orders:
                logger.warning(f"Received fill for unknown order: {client_order_id}")
                return

            order = self._in_flight_orders[client_order_id]
            order.add_fill(fill)

            logger.info(
                f"Fill processed: {client_order_id}",
                extra={
                    'client_order_id': client_order_id,
                    'fill_qty': str(fill.quantity),
                    'fill_price': str(fill.price),
                    'total_filled': str(order.filled_quantity),
                    'total_qty': str(order.quantity)
                }
            )

            # If fully filled, flag for stop_tracking (outside lock)
            if order.filled_quantity >= order.quantity:
                should_stop = True

        if should_stop:
            self.stop_tracking(client_order_id, "filled")
    
    def get_in_flight_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        """Get in-flight order by client ID"""
        return self._in_flight_orders.get(client_order_id)
    
    def get_in_flight_order_by_exchange_id(self, exchange_order_id: str) -> Optional[InFlightOrder]:
        """Get in-flight order by exchange ID"""
        client_id = self._exchange_id_to_client_id.get(exchange_order_id)
        if client_id:
            return self._in_flight_orders.get(client_id)
        return None
    
    def get_all_in_flight(self) -> List[InFlightOrder]:
        """Get all in-flight orders"""
        return list(self._in_flight_orders.values())
    
    def get_completed_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        """Get completed order"""
        return self._completed_orders.get(client_order_id)
    
    def get_orphaned_orders(self, broker_orders: Dict[str, dict]) -> List[str]:
        """
        Find orphaned orders (broker has, we don't).
        
        Args:
            broker_orders: Dict of exchange_order_id -> order info from broker
            
        Returns:
            List of exchange_order_ids that are orphans
        """
        broker_ids = set(broker_orders.keys())
        our_exchange_ids = set(self._exchange_id_to_client_id.keys())
        
        orphans = list(broker_ids - our_exchange_ids)
        
        if orphans:
            logger.warning(
                f"Detected {len(orphans)} orphaned orders",
                extra={'orphan_ids': orphans}
            )
        
        return orphans
    
    def get_shadow_orders(self, broker_orders: Dict[str, dict]) -> List[str]:
        """
        Find shadow orders (we have, broker doesn't).
        
        Args:
            broker_orders: Dict of exchange_order_id -> order info from broker
            
        Returns:
            List of client_order_ids that are shadows
        """
        broker_ids = set(broker_orders.keys())
        our_exchange_ids = set(self._exchange_id_to_client_id.keys())
        
        shadow_exchange_ids = our_exchange_ids - broker_ids
        
        # Convert to client IDs
        shadows = [
            self._exchange_id_to_client_id[ex_id]
            for ex_id in shadow_exchange_ids
        ]
        
        if shadows:
            logger.error(
                f"Detected {len(shadows)} shadow orders!",
                extra={'shadow_ids': shadows}
            )
        
        return shadows
    
    def get_stats(self) -> dict:
        """Get tracking statistics"""
        return {
            'in_flight_count': len(self._in_flight_orders),
            'completed_count': len(self._completed_orders),
            'total_tracked': len(self._in_flight_orders) + len(self._completed_orders),
            'in_flight_symbols': {
                order.symbol: sum(1 for o in self._in_flight_orders.values() if o.symbol == order.symbol)
                for order in self._in_flight_orders.values()
            }
        }
