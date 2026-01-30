"""
Order execution engine - bridges state machine and broker.

CRITICAL PROPERTIES:
1. Synchronous order submission
2. Status polling with timeout
3. State machine integration
4. Position store updates on fill
5. Order ID mapping (internal <-> broker)
6. Fill detection and notification

Based on LEAN's OrderTicket pattern.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
from decimal import Decimal
from datetime import datetime, timezone
import threading
import time

from core.logging import get_logger, LogStream, LogContext
from core.state import (
    OrderStateMachine,
    OrderStatus,
    OrderStateChangedEvent,
    Position,
    PositionStore
)
from core.state.order_tracker import OrderTracker, InFlightOrder, FillEvent, OrderSide as TrackerOrderSide, OrderType  # NEW
from core.brokers.alpaca_connector import (
    AlpacaBrokerConnector,
    BrokerOrderSide,
    BrokerOrderError
)
from core.events import OrderEventBus
from core.market.symbol_properties import SymbolPropertiesCache  # FIXED: Was SymbolPropertiesRegistry
from core.journal.trade_journal import TradeJournal, TradeIds, build_trade_event


# ============================================================================
# ORDER EXECUTION ENGINE
# ============================================================================

class OrderExecutionEngine:
    """
    Order execution engine.
    
    RESPONSIBILITIES:
    - Submit orders to broker
    - Poll order status
    - Transition state machine on status updates
    - Update position store on fills
    - Emit events
    
    THREAD SAFETY:
    - Thread-safe via internal locks
    - Can be called from multiple threads
    
    USAGE:
        engine = OrderExecutionEngine(
            broker=broker_connector,
            state_machine=state_machine,
            position_store=position_store
        )
        
        # Submit order
        broker_order_id = engine.submit_market_order(
            internal_order_id="ORD_001",
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="VWAPMeanReversion"
        )
        
        # Poll until filled or rejected
        final_status = engine.wait_for_order(broker_order_id, timeout=60)
    """
    
    def __init__(
        self,
        broker: AlpacaBrokerConnector,
        state_machine: OrderStateMachine,
        position_store: PositionStore,
        symbol_properties: Optional[SymbolPropertiesCache] = None,  # FIXED: Was SymbolPropertiesRegistry
        order_tracker: Optional[OrderTracker] = None  # NEW
    ):
        """Initialize execution engine."""
        self.broker = broker
        self.state_machine = state_machine
        self.position_store = position_store
        self.symbol_properties = symbol_properties  # NEW
        self.order_tracker = order_tracker  # NEW
        self.logger = get_logger(LogStream.ORDERS)
        self.trade_journal: Optional[TradeJournal] = None
        self._run_id: str = TradeJournal.new_run_id()
        self._trade_ids_by_internal: Dict[str, str] = {}

        # Track order metadata
        self._order_metadata: Dict[str, Dict] = {}
        self._metadata_lock = threading.Lock()
        
        # PATCH 2: Duplicate order prevention (engine-level defense)
        self._submitted_order_ids: set[str] = set()
        
        self.logger.info("OrderExecutionEngine initialized")
    def set_trade_journal(self, journal: TradeJournal, run_id: Optional[str] = None) -> None:
        self.trade_journal = journal
        if run_id:
            self._run_id = run_id

    def _trade_ids(self, internal_order_id: str) -> TradeIds:
        tid = self._trade_ids_by_internal.get(internal_order_id)
        if not tid:
            tid = TradeJournal.new_trade_id()
            self._trade_ids_by_internal[internal_order_id] = tid
        return TradeIds(run_id=self._run_id, trade_id=tid)

    def _j_emit(self, event: Dict[str, Any]) -> None:
        if self.trade_journal is None:
            return
        try:
            self.trade_journal.emit(event)
        except Exception:
            # journal must never kill execution
            pass

    def submit_market_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None
    ) -> str:
        """
        Submit market order to broker.
        
        Args:
            internal_order_id: Internal order ID
            symbol: Stock symbol
            quantity: Quantity to trade
            side: BUY or SELL
            strategy: Strategy name
            stop_loss: Optional stop loss price
            take_profit: Optional take profit price
            
        Returns:
            broker_order_id
            
        Raises:
            OrderExecutionError: If submission fails
        """
        with LogContext(internal_order_id):
            # PATCH 2: Check for duplicate order ID (engine-level protection)
            # This check MUST be outside try/except to avoid being wrapped
            if internal_order_id in self._submitted_order_ids:
                error_msg = f"DUPLICATE_ORDER: {internal_order_id} already submitted to broker"
                self.logger.error(error_msg)
                raise DuplicateOrderError(error_msg)
            
            try:
                
                # NEW: Validate and round order using symbol properties
                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        # Validate order
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=None,  # Market order, no price
                            side=side.value
                        )
                        
                        if not is_valid:
                            error_msg = f"Order validation failed: {reason}"
                            self.logger.error(error_msg)
                            raise OrderValidationError(error_msg)
                        
                        # Round quantity to lot size
                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                f"Quantity rounded: {quantity} -> {rounded_qty} "
                                f"(lot_size={props.lot_size})"
                            )
                            quantity = Decimal(str(rounded_qty))
                
                # Store metadata
                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "submitted_at": datetime.now(timezone.utc)  # PATCH 4: UTC-aware
                    }
                
                # Submit to broker
                broker_order_id = self.broker.submit_market_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    internal_order_id=internal_order_id
                )
                
                # PATCH 1: journal ORDER_SUBMIT (market)
                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="ORDER_SUBMIT",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    broker_order_id=broker_order_id,
                    symbol=symbol,
                    side=side.value,
                    qty=str(quantity),
                    order_type="MARKET",
                    strategy=strategy,
                    reason={"source": "engine"},
                ))

                # PATCH 2: Mark as submitted (prevent re-submission)
                self._submitted_order_ids.add(internal_order_id)
                
                # NEW: Track order in OrderTracker
                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                    client_order_id=internal_order_id,
                    exchange_order_id=broker_order_id,
                    symbol=symbol,
                    quantity=quantity,
                    side=tracker_side,
                    order_type=OrderType.MARKET,
                    price=None,
                    strategy_id=strategy,
                    submitted_at=datetime.now(timezone.utc),
                )

                    self.order_tracker.start_tracking(in_flight_order)
                    self.logger.info(
                        f"Order tracked: {internal_order_id} -> {broker_order_id}",
                        extra={"internal_order_id": internal_order_id}
                    )
                
                # Transition: PENDING -> SUBMITTED
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id
                )
                
                return broker_order_id
                
            except Exception as e:
                self.logger.error(
                    "Order submission failed",
                    extra={
                        "internal_order_id": internal_order_id,
                        "symbol": symbol,
                        "error": str(e)
                    },
                    exc_info=True
                )
                
                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="ERROR",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    symbol=symbol if "symbol" in locals() else None,
                    error={"where": "OrderExecutionEngine", "message": str(e)},
                ))

                # Transition to REJECTED
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e)
                )
                
                raise OrderExecutionError(f"Failed to submit order: {e}") from e
    
    def submit_limit_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        limit_price: Decimal,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None
    ) -> str:
        """
        Submit limit order to broker.

        IMPORTANT: This is designed for "one attempt only" entries:
        - Caller should wait/poll for fill up to TTL
        - If not filled, cancel (do NOT reprice/chase)

        Returns:
            broker_order_id
        """
        with LogContext(internal_order_id):
            # Duplicate ID guard (engine-level)
            if internal_order_id in self._submitted_order_ids:
                error_msg = f"DUPLICATE_ORDER: {internal_order_id} already submitted to broker"
                self.logger.error(error_msg)
                raise DuplicateOrderError(error_msg)

            try:
                if limit_price is None or limit_price <= 0:
                    raise OrderValidationError(f"limit_price must be positive, got {limit_price}")

                # Validate and round using symbol properties (if available)
                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=float(limit_price),
                            side=side.value
                        )
                        if not is_valid:
                            error_msg = f"Order validation failed: {reason}"
                            self.logger.error(error_msg)
                            raise OrderValidationError(error_msg)

                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                f"Quantity rounded: {quantity} -> {rounded_qty} (lot_size={props.lot_size})"
                            )
                            quantity = Decimal(str(rounded_qty))

                # Store metadata
                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "limit_price": limit_price,
                        "submitted_at": datetime.now(timezone.utc),
                    }

                # Submit to broker
                broker_order_id = self.broker.submit_limit_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    limit_price=limit_price,
                    internal_order_id=internal_order_id
                )
                # PATCH 1: journal ORDER_SUBMIT (limit)
                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="ORDER_SUBMIT",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    broker_order_id=broker_order_id,
                    symbol=symbol,
                    side=side.value,
                    qty=str(quantity),
                    order_type="LIMIT",
                    limit_price=str(limit_price),
                    strategy=strategy,
                    reason={"source": "engine"},
                ))

                self._submitted_order_ids.add(internal_order_id)

                # Track order in OrderTracker
                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                        client_order_id=internal_order_id,
                        exchange_order_id=broker_order_id,
                        symbol=symbol,
                        quantity=quantity,
                        side=tracker_side,
                        order_type=OrderType.LIMIT,
                        price=limit_price,
                        strategy_id=strategy,
                        submitted_at=datetime.now(timezone.utc)
                    )
                    self.order_tracker.start_tracking(in_flight_order)

                # Transition: PENDING -> SUBMITTED
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id
                )

                return broker_order_id

            except Exception as e:
                self.logger.error(
                    "Limit order submission failed",
                    extra={"internal_order_id": internal_order_id, "symbol": symbol, "error": str(e)},
                    exc_info=True
                )

                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="ERROR",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    symbol=symbol if "symbol" in locals() else None,
                    error={"where": "OrderExecutionEngine", "message": str(e)},
                ))

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e)
                )

                raise OrderExecutionError(f"Failed to submit limit order: {e}") from e

    def submit_stop_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        stop_price: Decimal,
        strategy: str
    ) -> str:
        """
        Submit protective stop order to broker.

        NOTE: Your InFlightOrder model does not include stop_price,
        so we track it as OrderType.STOP with price=None.
        """
        with LogContext(internal_order_id):
            if internal_order_id in self._submitted_order_ids:
                error_msg = f"DUPLICATE_ORDER: {internal_order_id} already submitted to broker"
                self.logger.error(error_msg)
                raise DuplicateOrderError(error_msg)

            try:
                if stop_price is None or stop_price <= 0:
                    raise OrderValidationError(f"stop_price must be positive, got {stop_price}")

                # Validate/round using symbol properties (if available)
                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=float(stop_price),
                            side=side.value
                        )
                        if not is_valid:
                            raise OrderValidationError(f"Stop order validation failed: {reason}")

                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                f"Stop qty rounded: {quantity} -> {rounded_qty} (lot_size={props.lot_size})"
                            )
                            quantity = Decimal(str(rounded_qty))

                # Store metadata
                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_price": stop_price,
                        "submitted_at": datetime.now(timezone.utc),
                    }

                broker_order_id = self.broker.submit_stop_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    stop_price=stop_price,
                    internal_order_id=internal_order_id
                )

                self._submitted_order_ids.add(internal_order_id)

                # Track in OrderTracker (no stop_price field on InFlightOrder in your build)
                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                        client_order_id=internal_order_id,
                        exchange_order_id=broker_order_id,
                        symbol=symbol,
                        quantity=quantity,
                        side=tracker_side,
                        order_type=OrderType.STOP,
                        price=None,
                        strategy_id=strategy,
                        submitted_at=datetime.now(timezone.utc),
                    )
                    self.order_tracker.start_tracking(in_flight_order)

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id
                )

                return broker_order_id

            except Exception as e:
                self.logger.error(
                    "Stop order submission failed",
                    extra={"internal_order_id": internal_order_id, "symbol": symbol, "error": str(e)},
                    exc_info=True
                )
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e)
                )
                raise OrderExecutionError(f"Failed to submit stop order: {e}") from e

    def cancel_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        reason: str = "cancelled_by_engine"
    ) -> bool:
        """Cancel an order at the broker and transition state machine."""
        with LogContext(internal_order_id):
            try:
                ok = self.broker.cancel_order(broker_order_id)
                if not ok:
                    return False

                # PATCH 1: journal CANCEL
                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="CANCEL",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    broker_order_id=broker_order_id,
                    symbol=self._order_metadata.get(internal_order_id, {}).get("symbol"),
                    reason={"cancel_reason": reason},
                ))

                # Transition to CANCELLED (best-effort; if already terminal, ignore)
                try:
                    order = self.state_machine.get_order(internal_order_id)
                    from_state = order.state if order else OrderStatus.SUBMITTED
                    if not self.state_machine.is_terminal(from_state):
                        self.state_machine.transition(
                            order_id=internal_order_id,
                            from_state=from_state,
                            to_state=OrderStatus.CANCELLED,
                            broker_order_id=broker_order_id,
                            reason=reason
                        )
                except Exception:
                    pass

                # Stop tracking
                if self.order_tracker:
                    self.order_tracker.stop_tracking(internal_order_id, reason="cancelled")

                return True
            except Exception as e:
                self.logger.error(
                    "Order cancel failed",
                    extra={"internal_order_id": internal_order_id, "broker_order_id": broker_order_id, "error": str(e)},
                    exc_info=True
                )
                ids = self._trade_ids(internal_order_id)
                self._j_emit(build_trade_event(
                    event_type="ERROR",
                    ids=ids,
                    internal_order_id=internal_order_id,
                    symbol=symbol if "symbol" in locals() else None,
                    error={"where": "OrderExecutionEngine", "message": str(e)},
                ))

                return False

    def get_fill_details(self, internal_order_id: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Return (filled_qty, fill_price) if known from state machine."""
        try:
            order = self.state_machine.get_order(internal_order_id)
            if not order:
                return None, None
            return getattr(order, "filled_qty", None), getattr(order, "fill_price", None)
        except Exception:
            return None, None

    # -------------------------------------------------------------------
    # PATCH 4: TTL / staleness helper (engine-level, uses submitted_at metadata)
    # -------------------------------------------------------------------
    def is_order_stale(self, internal_order_id: str, ttl_seconds: int) -> bool:
        """Return True if order age >= ttl_seconds based on engine submission metadata."""
        try:
            with self._metadata_lock:
                meta = self._order_metadata.get(internal_order_id) or {}
                submitted_at = meta.get("submitted_at")
            if not submitted_at:
                return False
            age = (datetime.now(timezone.utc) - submitted_at).total_seconds()
            return age >= float(ttl_seconds)
        except Exception:
            # Fail-safe: treat as stale to avoid leaking working orders
            return True

    def poll_order_status(
        self,
        internal_order_id: str,
        broker_order_id: str,
        current_state: OrderStatus
    ) -> OrderStatus:
        """
        Poll order status from broker and update state.
        
        Args:
            internal_order_id: Internal order ID
            broker_order_id: Broker order ID
            current_state: Current order state
            
        Returns:
            New order status
        """
        with LogContext(internal_order_id):
            try:
                # Get status from broker
                broker_status, fill_info = self.broker.get_order_status(broker_order_id)
                
                # If status changed, transition
                if broker_status != current_state:
                    self._handle_status_change(
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        from_state=current_state,
                        to_state=broker_status,
                        fill_info=fill_info
                    )
                
                return broker_status
                
            except Exception as e:
                self.logger.error(
                    "Failed to poll order status",
                    extra={
                        "internal_order_id": internal_order_id,
                        "broker_order_id": broker_order_id,
                        "error": str(e)
                    },
                    exc_info=True
                )
                return current_state
    
    def wait_for_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        timeout_seconds: int = 60,
        poll_interval: float = 1.0
    ) -> OrderStatus:
        """
        Wait for order to reach terminal state.
        
        Args:
            internal_order_id: Internal order ID
            broker_order_id: Broker order ID
            timeout_seconds: Max wait time
            poll_interval: Seconds between polls
            
        Returns:
            Final order status
        """
        with LogContext(internal_order_id):
            start = time.time()
            current_state = OrderStatus.SUBMITTED
            
            while time.time() - start < timeout_seconds:
                # Poll status
                current_state = self.poll_order_status(
                    internal_order_id=internal_order_id,
                    broker_order_id=broker_order_id,
                    current_state=current_state
                )
                
                # Check if terminal
                if self.state_machine.is_terminal(current_state):
                    self.logger.info(
                        f"Order reached terminal state: {current_state.value}",
                        extra={
                            "internal_order_id": internal_order_id,
                            "final_state": current_state.value,
                            "elapsed_seconds": time.time() - start
                        }
                    )
                    return current_state
                
                # Wait before next poll
                time.sleep(poll_interval)
            
            # Timeout
            self.logger.warning(
                "Order polling timeout",
                extra={
                    "internal_order_id": internal_order_id,
                    "timeout_seconds": timeout_seconds,
                    "last_state": current_state.value
                }
            )
            
            return current_state
    
    def _handle_status_change(
        self,
        internal_order_id: str,
        broker_order_id: str,
        from_state: OrderStatus,
        to_state: OrderStatus,
        fill_info: Optional[Dict]
    ):
        """Handle order status change."""
        # Extract fill details
        filled_qty = None
        fill_price = None
        
        if fill_info:
            filled_qty = fill_info.get("filled_qty")
            fill_price = fill_info.get("filled_avg_price")
        
        # Transition state
        self.state_machine.transition(
            order_id=internal_order_id,
            from_state=from_state,
            to_state=to_state,
            broker_order_id=broker_order_id,
            filled_qty=filled_qty,
            fill_price=fill_price
        )
        
        # NEW: Process fill in OrderTracker
        if to_state == OrderStatus.FILLED and self.order_tracker and filled_qty and fill_price:
            fill_event = FillEvent(
                timestamp=datetime.now(timezone.utc),
                quantity=filled_qty,
                price=fill_price,
                commission=Decimal("0"),  # Alpaca has zero commission
                fill_id=None
            )
            self.order_tracker.process_fill(internal_order_id, fill_event)
            self.logger.info(
                f"Fill processed in tracker: {internal_order_id} "
                f"{filled_qty} @ ${fill_price}",
                extra={"internal_order_id": internal_order_id}
            )
        
        # If filled, create/update position
        if to_state == OrderStatus.FILLED and filled_qty and fill_price:
            self._create_position(internal_order_id, filled_qty, fill_price)
    
    def _create_position(
        self,
        internal_order_id: str,
        filled_qty: Decimal,
        fill_price: Decimal
    ):
        """Create position on order fill."""
        # Get order metadata
        with self._metadata_lock:
            metadata = self._order_metadata.get(internal_order_id)
        
        if not metadata:
            self.logger.warning(
                f"No metadata for order {internal_order_id}",
                extra={"internal_order_id": internal_order_id}
            )
            return
        
        # Create position
        position = Position(
            symbol=metadata["symbol"],
            quantity=filled_qty,
            entry_price=fill_price,
            entry_time=datetime.now(timezone.utc),  # PATCH 4: UTC-aware
            strategy=metadata["strategy"],
            order_id=internal_order_id,
            stop_loss=metadata.get("stop_loss"),
            take_profit=metadata.get("take_profit")
        )
        
        # Store position
        self.position_store.upsert(position)
        
        self.logger.info(
            "Position created",
            extra={
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "entry_price": str(position.entry_price),
                "strategy": position.strategy
            }
        )


# ============================================================================
# EXCEPTIONS
# ============================================================================

class OrderExecutionError(Exception):
    """Order execution error."""
    pass


class DuplicateOrderError(OrderExecutionError):
    """Raised when attempting to submit duplicate order ID."""
    pass
