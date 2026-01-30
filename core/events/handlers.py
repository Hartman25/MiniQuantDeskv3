"""
Event Handlers - Wire events to OrderStateMachine and PositionStore.

UPDATED ARCHITECTURE:
- Uses OrderStateMachine.transition() for state changes
- Uses OrderStateMachine.get_order() for order retrieval
- Verifies orders exist before processing
- Handles missing orders gracefully
- Idempotent handlers (safe to replay)
"""

from typing import Callable, Dict, Type
from decimal import Decimal
import logging

from core.events.types import (
    OrderFilledEvent,
    OrderPartiallyFilledEvent,
    OrderCancelledEvent,
    OrderRejectedEvent,
    PositionClosedEvent,
    RiskLimitBreachedEvent,
    KillSwitchActivatedEvent
)
from core.state.order_machine import OrderStateMachine, OrderStatus
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog

logger = logging.getLogger(__name__)


class EventHandlerRegistry:
    """
    Event handler registry with OrderStateMachine integration.
    
    WIRING:
    - Receives events from EventBus
    - Calls OrderStateMachine.transition() to update state
    - Updates PositionStore with fills
    - Logs all actions to TransactionLog
    """
    
    def __init__(
        self,
        order_machine: OrderStateMachine,
        position_store: PositionStore,
        transaction_log: TransactionLog
    ):
        """Initialize with required components."""
        self.order_machine = order_machine
        self.position_store = position_store
        self.transaction_log = transaction_log
        
        self._handlers: Dict[Type, Callable] = {}
        
        logger.info("EventHandlerRegistry initialized")
    
    def register_handler(self, event_type: Type, handler: Callable) -> None:
        """Register handler for event type."""
        self._handlers[event_type] = handler
        logger.info(f"Registered handler for {event_type.__name__}")
    
    def handle_event(self, event) -> None:
        """Dispatch event to handler."""
        event_type = type(event)
        handler = self._handlers.get(event_type)
        
        if handler:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    f"Error handling {event_type.__name__}: {e}",
                    exc_info=True,
                    extra={'event': str(event)}
                )
        else:
            logger.debug(f"No handler for {event_type.__name__}")
    
    def register_default_handlers(self) -> None:
        """Register all default handlers."""
        self.register_handler(OrderFilledEvent, self._handle_order_filled)
        self.register_handler(OrderPartiallyFilledEvent, self._handle_order_partially_filled)
        self.register_handler(OrderCancelledEvent, self._handle_order_cancelled)
        self.register_handler(OrderRejectedEvent, self._handle_order_rejected)
        self.register_handler(PositionClosedEvent, self._handle_position_closed)
        self.register_handler(RiskLimitBreachedEvent, self._handle_risk_limit_breached)
        self.register_handler(KillSwitchActivatedEvent, self._handle_kill_switch)
        
        logger.info("Default event handlers registered")
    
    def wire_to_event_bus(self, event_bus) -> None:
        """Wire to EventBus for automatic dispatch."""
        # Subscribe to all events
        for event_type in self._handlers.keys():
            event_bus.subscribe(event_type, self._handlers[event_type])
        
        logger.info("EventHandlerRegistry wired to EventBus")
    
    # ========================================================================
    # ORDER HANDLERS
    # ========================================================================
    
    def _handle_order_filled(self, event: OrderFilledEvent) -> None:
        """
        Handle order fully filled.
        
        CRITICAL ACTIONS:
        1. Retrieve order from OrderStateMachine
        2. Determine current state
        3. Transition to FILLED
        4. Update PositionStore
        5. Log transaction
        """
        logger.info(
            f"[ORDER_FILLED] {event.order_id}: "
            f"{event.filled_quantity} {event.symbol} @ ${event.fill_price}"
        )
        
        # Get order from state machine
        order = self.order_machine.get_order(event.order_id)
        if not order:
            logger.error(
                f"Order {event.order_id} not found in OrderStateMachine",
                extra={'event': 'order_filled', 'order_id': event.order_id}
            )
            return
        
        # Transition to FILLED
        try:
            self.order_machine.transition(
                order_id=event.order_id,
                from_state=order.state,
                to_state=OrderStatus.FILLED,
                broker_order_id=event.broker_order_id,
                filled_qty=event.filled_quantity,
                fill_price=event.fill_price
            )
        except Exception as e:
            logger.error(
                f"Failed to transition order {event.order_id} to FILLED: {e}",
                exc_info=True
            )
            return
        
        # Log transaction
        self.transaction_log.append({
            'event_type': 'order_filled',
            'timestamp': event.timestamp.isoformat(),
            'order_id': event.order_id,
            'symbol': event.symbol,
            'quantity': str(event.filled_quantity),
            'fill_price': str(event.fill_price),
            'commission': str(event.commission),
            'total_cost': str(event.total_cost)
        })
        
        # Update position
        quantity = event.filled_quantity if order.side == "LONG" else -event.filled_quantity
        
        try:
            self.position_store.add_or_update(
                symbol=event.symbol,
                quantity=quantity,
                entry_price=event.fill_price,
                strategy=order.strategy,
                order_id=event.order_id
            )
        except Exception as e:
            logger.error(
                f"Failed to update position for {event.symbol}: {e}",
                exc_info=True
            )
    
    def _handle_order_partially_filled(self, event: OrderPartiallyFilledEvent) -> None:
        """
        Handle partial fill.
        
        ACTIONS:
        1. Get order state
        2. Transition to PARTIALLY_FILLED
        3. Update position with partial qty
        """
        logger.info(
            f"[ORDER_PARTIAL] {event.order_id}: "
            f"{event.filled_quantity} filled, {event.remaining_quantity} remaining"
        )
        
        order = self.order_machine.get_order(event.order_id)
        if not order:
            logger.error(f"Order {event.order_id} not found for partial fill")
            return
        
        # Transition to PARTIALLY_FILLED
        try:
            self.order_machine.transition(
                order_id=event.order_id,
                from_state=order.state,
                to_state=OrderStatus.PARTIALLY_FILLED,
                broker_order_id=event.broker_order_id,
                filled_qty=event.filled_quantity,
                remaining_qty=event.remaining_quantity,
                fill_price=event.fill_price
            )
        except Exception as e:
            logger.error(f"Failed to transition to PARTIALLY_FILLED: {e}", exc_info=True)
            return
        
        # Log transaction
        self.transaction_log.append({
            'event_type': 'order_partially_filled',
            'timestamp': event.timestamp.isoformat(),
            'order_id': event.order_id,
            'symbol': event.symbol,
            'filled_quantity': str(event.filled_quantity),
            'remaining_quantity': str(event.remaining_quantity),
            'fill_price': str(event.fill_price)
        })
        
        # Update position
        quantity = event.filled_quantity if order.side == "LONG" else -event.filled_quantity
        
        try:
            self.position_store.add_or_update(
                symbol=event.symbol,
                quantity=quantity,
                entry_price=event.fill_price,
                strategy=order.strategy,
                order_id=event.order_id
            )
        except Exception as e:
            logger.error(f"Failed to update position: {e}", exc_info=True)
    
    def _handle_order_cancelled(self, event: OrderCancelledEvent) -> None:
        """Handle order cancellation."""
        logger.info(f"[ORDER_CANCELLED] {event.order_id}: {event.reason}")
        
        order = self.order_machine.get_order(event.order_id)
        if not order:
            logger.warning(f"Order {event.order_id} not found for cancellation")
            return
        
        # Transition to CANCELLED
        try:
            self.order_machine.transition(
                order_id=event.order_id,
                from_state=order.state,
                to_state=OrderStatus.CANCELLED,
                broker_order_id=event.broker_order_id,
                reason=event.reason
            )
        except Exception as e:
            logger.error(f"Failed to transition to CANCELLED: {e}", exc_info=True)
            return
        
        # Log
        self.transaction_log.append({
            'event_type': 'order_cancelled',
            'timestamp': event.timestamp.isoformat(),
            'order_id': event.order_id,
            'symbol': event.symbol,
            'reason': event.reason
        })
    
    def _handle_order_rejected(self, event: OrderRejectedEvent) -> None:
        """Handle order rejection."""
        logger.warning(
            f"[ORDER_REJECTED] {event.order_id}: {event.reason} "
            f"(rejected by {event.rejected_by})"
        )
        
        order = self.order_machine.get_order(event.order_id)
        if not order:
            logger.error(f"Order {event.order_id} not found for rejection")
            return
        
        # Transition to REJECTED
        try:
            self.order_machine.transition(
                order_id=event.order_id,
                from_state=order.state,
                to_state=OrderStatus.REJECTED,
                reason=event.reason
            )
        except Exception as e:
            logger.error(f"Failed to transition to REJECTED: {e}", exc_info=True)
            return
        
        # Log
        self.transaction_log.append({
            'event_type': 'order_rejected',
            'timestamp': event.timestamp.isoformat(),
            'order_id': event.order_id,
            'symbol': event.symbol,
            'reason': event.reason,
            'rejected_by': event.rejected_by
        })
    
    # ========================================================================
    # POSITION HANDLERS
    # ========================================================================
    
    def _handle_position_closed(self, event: PositionClosedEvent) -> None:
        """Handle position closure - log P&L."""
        logger.info(
            f"[POSITION_CLOSED] {event.symbol}: "
            f"P&L ${event.realized_pnl} "
            f"({event.closed_reason})"
        )
        
        self.transaction_log.append({
            'event_type': 'position_closed',
            'timestamp': event.timestamp.isoformat(),
            'position_id': event.position_id,
            'symbol': event.symbol,
            'quantity': str(event.quantity),
            'entry_price': str(event.entry_price),
            'exit_price': str(event.exit_price),
            'realized_pnl': str(event.realized_pnl),
            'commission': str(event.commission),
            'hold_duration_seconds': event.hold_duration_seconds,
            'closed_reason': event.closed_reason
        })
    
    # ========================================================================
    # RISK HANDLERS
    # ========================================================================
    
    def _handle_risk_limit_breached(self, event: RiskLimitBreachedEvent) -> None:
        """Handle risk limit breach - log critical alert."""
        logger.critical(
            f"[RISK_LIMIT_BREACHED] {event.limit_type}: "
            f"${event.current_value} exceeds limit ${event.limit_value}"
        )
        
        self.transaction_log.append({
            'event_type': 'risk_limit_breached',
            'timestamp': event.timestamp.isoformat(),
            'limit_type': event.limit_type,
            'current_value': str(event.current_value),
            'limit_value': str(event.limit_value),
            'symbol': event.symbol,
            'action_taken': event.action_taken
        })
    
    def _handle_kill_switch(self, event: KillSwitchActivatedEvent) -> None:
        """Handle emergency kill switch activation."""
        logger.critical(
            f"[KILL_SWITCH] Activated: {event.reason} "
            f"(positions closed: {event.all_positions_closed}, "
            f"orders cancelled: {event.all_orders_cancelled})"
        )
        
        self.transaction_log.append({
            'event_type': 'kill_switch_activated',
            'timestamp': event.timestamp.isoformat(),
            'reason': event.reason,
            'trigger_source': event.trigger_source,
            'all_positions_closed': event.all_positions_closed,
            'all_orders_cancelled': event.all_orders_cancelled
        })
