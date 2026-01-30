"""
Event bridge - connects EventBus to Discord notifier.

ARCHITECTURE:
- Subscribe to OrderEventBus events
- Transform events to notifications
- Filter by importance
- Route to appropriate channels

Based on event-driven notification patterns.
"""

from typing import Dict, Optional, Any
from decimal import Decimal
from datetime import datetime

from core.events import OrderEventBus, OrderStateChangedEvent
from core.state import OrderStatus
from core.discord.notifier import DiscordNotifier, NotificationChannel
from core.logging import get_logger, LogStream


# ============================================================================
# DISCORD EVENT BRIDGE
# ============================================================================

class DiscordEventBridge:
    """
    Bridge between EventBus and Discord notifier.
    
    RESPONSIBILITIES:
    - Subscribe to all order events
    - Transform to Discord notifications
    - Filter spam (only important events)
    - Route to correct channels
    
    USAGE:
        bridge = DiscordEventBridge(
            event_bus=event_bus,
            notifier=notifier
        )
        
        bridge.start()
    """
    
    def __init__(
        self,
        event_bus: OrderEventBus,
        notifier: DiscordNotifier
    ):
        """
        Initialize bridge.
        
        Args:
            event_bus: Order event bus
            notifier: Discord notifier
        """
        self.event_bus = event_bus
        self.notifier = notifier
        self.logger = get_logger(LogStream.SYSTEM)
        
        self.logger.info("DiscordEventBridge initialized")
    
    def start(self):
        """Start listening to events."""
        # Subscribe to order state changes
        self.event_bus.subscribe(OrderStateChangedEvent, self._handle_state_change)
        
        self.logger.info("Discord event bridge started")
    
    def stop(self):
        """Stop listening to events."""
        # Note: Current EventBus doesn't support unsubscribe
        # In production, would need to add unsubscribe method
        self.logger.info("Discord event bridge stopped")
    
    def _handle_state_change(self, event: OrderStateChangedEvent):
        """
        Handle order state change event.
        
        Args:
            event: OrderStateChangedEvent
        """
        try:
            order_id = event.order_id
            old_status = event.from_state
            new_status = event.to_state
            
            # Extract metadata from event attributes
            # Note: Actual implementation would need access to order metadata
            # For now, use placeholder values
            symbol = "UNKNOWN"
            quantity = Decimal("0")
            side = "UNKNOWN"
            
            # SUBMITTED - order sent to broker
            if new_status == OrderStatus.SUBMITTED:
                self.notifier.send_order_submitted(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_id=order_id
                )
            
            # FILLED - order executed
            elif new_status == OrderStatus.FILLED:
                self.notifier.send_trade_execution(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=Decimal("0"),  # Would get from fill info
                    order_id=order_id
                )
            
            # REJECTED - order rejected
            elif new_status == OrderStatus.REJECTED:
                self.notifier.send_error(
                    error=f"Order rejected: {symbol}",
                    details=f"Order ID: {order_id}"
                )
            
        except Exception as e:
            self.logger.error("Event handling error", extra={
                "error": str(e),
                "order_id": event.order_id
            }, exc_info=True)
