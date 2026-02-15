"""
Thread-safe event bus for order lifecycle events.

CRITICAL PROPERTIES:
1. Thread-safe via queue (producer-consumer pattern)
2. Handlers registered by event type
3. FIFO event processing
4. Handler failures are isolated (logged but don't crash bus)
5. Graceful shutdown with queue drain
6. Runs in dedicated thread

Based on observer pattern with thread-safety guarantees.
"""

import threading
import queue
import os
import atexit
import weakref
from typing import Dict, List, Callable, Any, Type, Optional
from dataclasses import dataclass, field
import logging
from datetime import datetime
from decimal import Decimal

from core.logging import get_logger, LogStream

# ============================================================================
# GLOBAL REGISTRY (helps tests / interpreter shutdown)
# ============================================================================

# Weak registry of all instantiated buses so we can stop them at interpreter exit.
_BUS_REGISTRY: "weakref.WeakSet[OrderEventBus]" = weakref.WeakSet()  # type: ignore[name-defined]

def _stop_all_buses_at_exit() -> None:
    # Best-effort cleanup so stray non-daemon threads don't hang pytest / interpreter shutdown.
    for bus in list(_BUS_REGISTRY):
        try:
            if getattr(bus, "_running", False):
                bus.stop(timeout=0.2)
        except Exception:
            # Never raise during interpreter shutdown
            pass

atexit.register(_stop_all_buses_at_exit)


# ============================================================================
# EVENT BASE CLASS
# ============================================================================

@dataclass
class Event:
    """Base class for all events."""
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        raise NotImplementedError("Subclasses must implement to_dict()")


# ============================================================================
# ORDER EVENTS
# ============================================================================

@dataclass
class OrderStateChangedEvent(Event):
    """Event emitted when order state changes."""
    order_id: str
    from_state: 'OrderStatus'
    to_state: 'OrderStatus'
    broker_order_id: Optional[str] = None
    filled_qty: Optional['Decimal'] = None
    fill_price: Optional['Decimal'] = None
    reason: Optional[str] = None
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        """Convert to dict."""
        from decimal import Decimal
        return {
            "order_id": self.order_id,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "broker_order_id": self.broker_order_id,
            "filled_qty": str(self.filled_qty) if self.filled_qty else None,
            "fill_price": str(self.fill_price) if self.fill_price else None,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat()
        }


# ============================================================================
# EVENT BUS
# ============================================================================

class OrderEventBus:
    """
    Thread-safe event bus for distributing order events.
    
    ARCHITECTURE:
    - Producers call emit(event) from any thread
    - Events queued in thread-safe queue
    - Dedicated consumer thread processes events FIFO
    - Each event type has registered handlers
    - Handler failures logged but don't crash bus
    
    THREAD SAFETY:
    - emit() is thread-safe (uses queue.Queue)
    - subscribe() must be called before start()
    - Handlers execute in event bus thread (NOT caller thread)
    
    USAGE:
        bus = OrderEventBus()
        bus.subscribe(OrderStateChangedEvent, handle_order_state_change)
        bus.start()
        
        # From any thread:
        bus.emit(OrderStateChangedEvent(...))
        
        # Shutdown:
        bus.stop()
    """
    
    def __init__(self, max_queue_size: int = 10000, *, daemon: bool | None = None):
        """
        Initialize event bus.
        
        Args:
            max_queue_size: Maximum events in queue (prevents memory leak)
        """
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Event queue (thread-safe)
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        
        # Event handlers: event_type -> list of handlers
        self._handlers: Dict[Type[Event], List[Callable]] = {}
        
        # Control
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        
        

        # Thread daemon mode:
        # - In production we default to non-daemon for graceful shutdown.
        # - Under pytest, stray non-daemon threads can hang the test runner.
        if daemon is None:
            daemon = bool(os.environ.get('PYTEST_CURRENT_TEST') or os.environ.get('PYTEST_RUNNING'))
        self._daemon = bool(daemon)

        # Track instances for best-effort cleanup at interpreter shutdown.
        try:
            _BUS_REGISTRY.add(self)  # type: ignore[arg-type]
        except Exception:
            pass
# Statistics
        self._events_processed = 0
        self._events_failed = 0
        self._events_dropped = 0  # PATCH 8: track dropped events
        
        self.logger.info("OrderEventBus initialized", extra={
            "max_queue_size": max_queue_size
        })
    
    def subscribe(self, event_type: Type[Event], handler: Callable[[Event], None]):
        """
        Register handler for event type.
        
        Args:
            event_type: Event class to subscribe to
            handler: Callable that accepts event
            
        Raises:
            RuntimeError: If bus is already running
            
        Example:
            def handle_state_change(event: OrderStateChangedEvent):
                print(f"Order {event.order_id} changed state")
            
            bus.subscribe(OrderStateChangedEvent, handle_state_change)
        """
        if self._running:
            raise RuntimeError("Cannot subscribe while bus is running")
        
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        self._handlers[event_type].append(handler)
        
        self.logger.debug(f"Handler registered for {event_type.__name__}", extra={
            "event_type": event_type.__name__,
            "handler_count": len(self._handlers[event_type])
        })
    
    def emit(self, event: Event):
        """
        Emit event to bus.

        Thread-safe. Can be called from any thread.
        Non-fatal: if the queue is full the event is dropped and the
        dropped counter is incremented (PATCH 8).

        Args:
            event: Event to emit
        """
        if not self._running:
            raise RuntimeError("Event bus is not running. Call start() first.")

        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._events_dropped += 1
            self.logger.warning(
                "Event queue full, dropping event (dropped=%d)",
                self._events_dropped,
                extra={
                    "event_type": type(event).__name__,
                    "queue_size": self._queue.qsize(),
                    "events_dropped": self._events_dropped,
                },
            )
    
    def start(self):
        """
        Start event bus processing thread.
        
        Raises:
            RuntimeError: If already running
        """
        if self._running:
            raise RuntimeError("Event bus already running")
        
        self._stop_event.clear()
        self._running = True
        
        # Start consumer thread
        self._thread = threading.Thread(
            target=self._process_events,
            name="OrderEventBus",
            daemon=self._daemon  # auto-daemon under pytest to avoid hangs
        )
        self._thread.start()
        
        self.logger.info("OrderEventBus started", extra={
            "thread_id": self._thread.ident,
            "registered_event_types": len(self._handlers)
        })
    
    def stop(self, timeout: float = 5.0):
        """
        Stop event bus and drain queue.
        
        Args:
            timeout: Max seconds to wait for queue drain
            
        Raises:
            RuntimeError: If not running
        """
        if not self._running:
            raise RuntimeError("Event bus not running")
        
        self.logger.info("Stopping OrderEventBus...", extra={
            "queue_size": self._queue.qsize(),
            "events_processed": self._events_processed,
            "events_failed": self._events_failed
        })
        
        # Signal stop
        self._stop_event.set()
        
        # Wait for thread to finish
        if self._thread:
            self._thread.join(timeout=timeout)
            
            if self._thread.is_alive():
                self.logger.warning(
                    "Event bus thread did not stop cleanly",
                    extra={"timeout": timeout}
                )
        
        self._running = False
        
        self.logger.info("OrderEventBus stopped", extra={
            "events_processed": self._events_processed,
            "events_failed": self._events_failed,
            "queue_remaining": self._queue.qsize()
        })
    
    def _process_events(self):
        """
        Event processing loop (runs in dedicated thread).
        
        Processes events FIFO until stop signal received.
        """
        self.logger.info("Event processing thread started")
        
        while not self._stop_event.is_set():
            try:
                # Get event with timeout (allows checking stop_event)
                event = self._queue.get(block=True, timeout=0.1)
                
                # Process event
                self._dispatch_event(event)
                
                # Mark task done
                self._queue.task_done()
                
                self._events_processed += 1
                
            except queue.Empty:
                # No events, continue loop
                continue
            
            except Exception as e:
                self.logger.error(
                    "Unexpected error in event processing loop",
                    extra={"error": str(e)},
                    exc_info=True
                )
                self._events_failed += 1
        
        # Drain remaining events
        self.logger.info("Draining remaining events...", extra={
            "remaining": self._queue.qsize()
        })
        
        drained = 0
        while True:
            try:
                event = self._queue.get(block=False)
                self._dispatch_event(event)
                self._queue.task_done()
                drained += 1
            except queue.Empty:
                break
        
        self.logger.info(f"Event processing thread stopped (drained {drained} events)")
    
    def _dispatch_event(self, event: Event):
        """
        Dispatch event to registered handlers.
        
        Handler failures are logged but don't crash bus.
        
        Args:
            event: Event to dispatch
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        
        if not handlers:
            self.logger.debug(f"No handlers for {event_type.__name__}", extra={
                "event_type": event_type.__name__
            })
            return
        
        # Call each handler
        for handler in handlers:
            try:
                handler(event)
                
            except Exception as e:
                # CRITICAL: Handler failure does NOT crash bus
                self.logger.error(
                    f"Handler failed for {event_type.__name__}",
                    extra={
                        "event_type": event_type.__name__,
                        "handler": handler.__name__,
                        "error": str(e)
                    },
                    exc_info=True
                )
                self._events_failed += 1
    
    def __del__(self):
        """Best-effort cleanup if user forgot to call stop()."""
        try:
            if getattr(self, "_running", False):
                self.stop(timeout=0.1)
        except Exception:
            pass

    def get_stats(self) -> Dict:
        """
        Get event bus statistics.

        Returns:
            Dict with processed, failed, dropped, queue size
        """
        return {
            "events_processed": self._events_processed,
            "events_failed": self._events_failed,
            "events_dropped": self._events_dropped,
            "queue_size": self._queue.qsize(),
            "running": self._running,
            "registered_event_types": len(self._handlers),
        }
    
    def __enter__(self):
        """Context manager support."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support."""
        self.stop()
