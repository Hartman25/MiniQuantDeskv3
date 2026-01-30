"""
Event bus for order lifecycle events.

Provides thread-safe event distribution with FIFO processing.
"""

from .bus import (
    OrderEventBus,
    Event,
    OrderStateChangedEvent,
)

__all__ = [
    "OrderEventBus",
    "Event",
    "OrderStateChangedEvent",
]
