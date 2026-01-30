"""
Real-time market data and event-driven execution.
"""

# Legacy real-time data
from .data import (
    RealtimeDataHandler,
    QuoteAggregator,
)

from .executor import (
    EventDrivenExecutor,
)

# NEW: User stream tracker (real-time WebSocket fills)
from .user_stream_tracker import UserStreamTracker, StreamEventType

__all__ = [
    "RealtimeDataHandler",
    "QuoteAggregator",
    "EventDrivenExecutor",
    "UserStreamTracker",  # NEW
    "StreamEventType",  # NEW
]
