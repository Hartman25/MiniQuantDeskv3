"""
Event-driven execution engine - reacts to real-time signals.

ARCHITECTURE:
- Signal queue (thread-safe)
- Async execution workers
- Position tracking
- Order lifecycle management
- Event callbacks

Based on reactive execution pattern.
"""

from typing import Callable, Optional, Dict, List
from decimal import Decimal
from datetime import datetime, timezone
import threading
import queue
import time

from core.logging import get_logger, LogStream
from strategies.signals import TradingSignal, SignalType
from core.execution import OrderExecutionEngine
from core.brokers import BrokerOrderSide
from core.state import OrderStatus


# ============================================================================
# EVENT-DRIVEN EXECUTOR
# ============================================================================

class EventDrivenExecutor:
    """
    Event-driven execution engine.
    
    FEATURES:
    - Signal queue
    - Worker threads
    - Async execution
    - Event callbacks
    - Order tracking
    
    USAGE:
        executor = EventDrivenExecutor(
            execution_engine=exec_engine,
            max_workers=3
        )
        
        @executor.on_fill
        def handle_fill(order_id, symbol, qty, price):
            print(f"Filled: {symbol} {qty} @ {price}")
        
        executor.start()
        
        # Submit signal
        executor.submit_signal(signal)
    """
    
    def __init__(
        self,
        execution_engine: OrderExecutionEngine,
        max_workers: int = 2
    ):
        """Initialize executor."""
        self.execution_engine = execution_engine
        self.max_workers = max_workers
        self.logger = get_logger(LogStream.ORDERS)
        
        # Signal queue
        self._signal_queue: queue.Queue = queue.Queue()
        
        # Workers
        self._workers: List[threading.Thread] = []
        self._running = False
        
        # Callbacks
        self._fill_callbacks: List[Callable] = []
        self._reject_callbacks: List[Callable] = []
        
        # Active orders
        self._active_orders: Dict[str, Dict] = {}
        self._orders_lock = threading.Lock()
        
        self.logger.info("EventDrivenExecutor initialized", extra={
            "max_workers": max_workers
        })
    
    def submit_signal(self, signal: TradingSignal, quantity: Decimal, price: Decimal):
        """
        Submit signal for execution.
        
        Args:
            signal: Trading signal
            quantity: Quantity to trade
            price: Target price
        """
        self._signal_queue.put({
            "signal": signal,
            "quantity": quantity,
            "price": price,
            "submitted_at": datetime.now(timezone.utc)
        })
        
        self.logger.info(f"Signal queued: {signal.symbol}", extra={
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "quantity": str(quantity)
        })
    
    def on_fill(self, callback: Callable):
        """Register fill callback."""
        self._fill_callbacks.append(callback)
        return callback
    
    def on_reject(self, callback: Callable):
        """Register reject callback."""
        self._reject_callbacks.append(callback)
        return callback
    
    def start(self):
        """Start executor."""
        if self._running:
            return
        
        self._running = True
        
        # Start workers
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                args=(i,),
                daemon=True
            )
            worker.start()
            self._workers.append(worker)
        
        self.logger.info(f"Started {self.max_workers} workers")
    
    def stop(self, timeout: float = 10.0):
        """Stop executor."""
        if not self._running:
            return
        
        self._running = False
        
        # Wait for workers
        for worker in self._workers:
            worker.join(timeout=timeout)
        
        self.logger.info("Executor stopped")
    
    def _worker_loop(self, worker_id: int):
        """Worker thread loop."""
        self.logger.info(f"Worker {worker_id} started")
        
        while self._running:
            try:
                # Get signal (with timeout)
                try:
                    item = self._signal_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Execute
                self._execute_signal(item, worker_id)
                
            except Exception as e:
                self.logger.error(f"Worker {worker_id} error", extra={
                    "error": str(e)
                }, exc_info=True)
        
        self.logger.info(f"Worker {worker_id} stopped")
    
    def _execute_signal(self, item: Dict, worker_id: int):
        """Execute signal."""
        signal = item["signal"]
        quantity = item["quantity"]
        price = item["price"]
        
        self.logger.info(f"Worker {worker_id} executing: {signal.symbol}", extra={
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value
        })
        
        try:
            # Convert signal to order side
            if signal.signal_type == SignalType.LONG:
                side = BrokerOrderSide.BUY
            elif signal.signal_type == SignalType.SHORT:
                side = BrokerOrderSide.SELL
            else:
                return  # FLAT/HOLD
            
            # Generate order ID
            internal_order_id = f"ORD_{int(datetime.now(timezone.utc).timestamp()*1000)}"
            
            # Submit order
            broker_order_id = self.execution_engine.submit_market_order(
                internal_order_id=internal_order_id,
                symbol=signal.symbol,
                quantity=quantity,
                side=side,
                strategy=signal.strategy,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit
            )
            
            # Track order
            with self._orders_lock:
                self._active_orders[internal_order_id] = {
                    "broker_order_id": broker_order_id,
                    "symbol": signal.symbol,
                    "quantity": quantity,
                    "side": side,
                    "submitted_at": datetime.now(timezone.utc)
                }
            
            # Wait for fill
            final_status = self.execution_engine.wait_for_order(
                internal_order_id=internal_order_id,
                broker_order_id=broker_order_id,
                timeout_seconds=30
            )
            
            # Handle result
            if final_status == OrderStatus.FILLED:
                self._handle_fill(internal_order_id)
            else:
                self._handle_reject(internal_order_id, final_status)
            
        except Exception as e:
            self.logger.error(f"Execution failed: {signal.symbol}", extra={
                "error": str(e)
            }, exc_info=True)
    
    def _handle_fill(self, internal_order_id: str):
        """Handle order fill."""
        with self._orders_lock:
            order_info = self._active_orders.pop(internal_order_id, None)
        
        if not order_info:
            return
        
        # Call callbacks
        for callback in self._fill_callbacks:
            try:
                callback(
                    order_id=internal_order_id,
                    symbol=order_info["symbol"],
                    quantity=order_info["quantity"],
                    price=None  # TODO: Get from fill info
                )
            except Exception as e:
                self.logger.error("Fill callback error", extra={
                    "error": str(e)
                }, exc_info=True)
    
    def _handle_reject(self, internal_order_id: str, status: OrderStatus):
        """Handle order rejection."""
        with self._orders_lock:
            order_info = self._active_orders.pop(internal_order_id, None)
        
        if not order_info:
            return
        
        # Call callbacks
        for callback in self._reject_callbacks:
            try:
                callback(
                    order_id=internal_order_id,
                    symbol=order_info["symbol"],
                    reason=status.value
                )
            except Exception as e:
                self.logger.error("Reject callback error", extra={
                    "error": str(e)
                }, exc_info=True)
