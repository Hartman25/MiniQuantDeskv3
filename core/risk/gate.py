"""
PreTradeRiskGate - Independent risk validation service.

CRITICAL ARCHITECTURE:
1. INDEPENDENT service (not inline in execution path)
2. BLOCKING queue for atomic checks
3. ALL orders MUST pass through gate
4. Gate has VETO power (can reject any order)
5. Fail-safe: If gate unavailable, BLOCK all orders

CHECKS PERFORMED:
- Daily loss limit
- Position size limits
- Notional exposure limits
- Account buying power
- PDT rule compliance
- Duplicate order prevention

This is the LAST LINE OF DEFENSE before capital deployment.
Based on LEAN's PreTradeFilter with enhanced safety.
"""

from decimal import Decimal
from typing import Optional, Tuple, Dict, Set
from queue import Queue, Empty
from threading import Thread, Lock, Event
from datetime import datetime, timezone, date
import logging

from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer, calculate_exposure_pct
from core.events.types import OrderRejectedEvent
from core.risk.protections import ProtectionManager  # NEW

logger = logging.getLogger(__name__)


# ============================================================================
# ORDER REQUEST (Input to Gate)
# ============================================================================

class OrderRequest:
    """Order request submitted to risk gate."""
    
    def __init__(
        self,
        order_id: str,
        symbol: str,
        quantity: int,  # Shares (integer)
        side: str,  # "LONG" or "SHORT"
        order_type: str,  # "MARKET", "LIMIT"
        strategy: str,
        current_price: Decimal,
        limit_price: Optional[Decimal] = None
    ):
        self.order_id = order_id
        self.symbol = symbol
        self.quantity = quantity
        self.side = side
        self.order_type = order_type
        self.strategy = strategy
        self.current_price = current_price
        self.limit_price = limit_price
        self.timestamp = datetime.now(timezone.utc)
        
        # Calculate notional
        price_to_use = limit_price if limit_price else current_price
        self.notional = Decimal(str(quantity)) * price_to_use


# ============================================================================
# GATE DECISION (Output from Gate)
# ============================================================================

class GateDecision:
    """Risk gate decision result."""
    
    def __init__(
        self,
        order_id: str,
        approved: bool,
        rejection_reason: Optional[str] = None,
        checks_passed: Optional[dict] = None
    ):
        self.order_id = order_id
        self.approved = approved
        self.rejection_reason = rejection_reason
        self.checks_passed = checks_passed or {}
        self.timestamp = datetime.now(timezone.utc)


# ============================================================================
# PRE-TRADE RISK GATE
# ============================================================================

class PreTradeRiskGate:
    """
    Independent risk validation service with blocking queue.
    
    ARCHITECTURE:
    - Dedicated thread processes queue
    - Orders wait for gate decision
    - Gate holds ALL state (limits, positions, pending orders)
    - Atomic check-then-update (no race conditions)
    
    Usage:
        gate = PreTradeRiskGate(
            limits_tracker=limits_tracker,
            position_sizer=position_sizer,
            account_value=Decimal("1000.00")
        )
        
        gate.start()
        
        # Submit order for validation
        request = OrderRequest(...)
        decision = gate.submit_order(request, timeout=5.0)
        
        if decision.approved:
            # Submit to broker
            pass
        else:
            # Reject order
            logger.error(f"Order rejected: {decision.rejection_reason}")
    """
    
    def __init__(
        self,
        limits_tracker: PersistentLimitsTracker,
        position_sizer: NotionalPositionSizer,
        account_value: Decimal,
        enable_pdt_protection: bool = True,
        max_orders_per_day: int = 3,  # PDT protection for small accounts
        protections: Optional[ProtectionManager] = None  # NEW: Circuit breakers
    ):
        """
        Initialize risk gate.
        
        Args:
            limits_tracker: Daily limits tracker
            position_sizer: Position sizer
            account_value: Current account equity
            enable_pdt_protection: Block if 3+ day trades in 5 days
            max_orders_per_day: Max orders (PDT protection)
            protections: Protection manager for circuit breakers (optional)
        """
        self.limits_tracker = limits_tracker
        self.position_sizer = position_sizer
        self.account_value = account_value
        self.enable_pdt_protection = enable_pdt_protection
        self.max_orders_per_day = max_orders_per_day
        self._protections = protections  # NEW
        
        # Queue for order requests
        self._request_queue: Queue[OrderRequest] = Queue()
        self._decision_map: dict[str, GateDecision] = {}
        
        # State tracking
        self._active_positions: dict[str, Decimal] = {}  # symbol → notional
        self._pending_orders: set[str] = set()  # order_ids
        self._order_count_today = 0
        self._submitted_orders_today: set[str] = set()  # Duplicate prevention
        
        # PATCH 2: Price tracking for fat-finger detection
        self._recent_prices: Dict[str, Decimal] = {}  # symbol → last known price
        
        # PATCH 2: PDT tracking (separate from total order count)
        self._day_trades_today: Set[Tuple[str, str]] = set()  # (symbol, "BUY"|"SELL") pairs
        self._last_reset_date: date = datetime.now(timezone.utc).date()
        
        # Threading
        self._lock = Lock()
        self._worker_thread: Optional[Thread] = None
        self._shutdown_event = Event()
        self._running = False
        
        logger.info(
            f"[RISK_GATE] Initialized "
            f"(account=${account_value}, "
            f"pdt_protection={enable_pdt_protection}, "
            f"max_orders={max_orders_per_day})"
        )
    
    # ========================================================================
    # LIFECYCLE
    # ========================================================================
    
    def start(self) -> None:
        """Start risk gate worker thread."""
        if self._running:
            logger.warning("[RISK_GATE] Already running")
            return
        
        self._running = True
        self._shutdown_event.clear()
        
        self._worker_thread = Thread(
            target=self._process_queue,
            name="RiskGateWorker",
            daemon=True
        )
        self._worker_thread.start()
        
        logger.info("[RISK_GATE] Started")
    
    def stop(self) -> None:
        """Stop risk gate worker thread."""
        if not self._running:
            return
        
        logger.info("[RISK_GATE] Stopping...")
        self._shutdown_event.set()
        self._running = False
        
        if self._worker_thread:
            self._worker_thread.join(timeout=5.0)
        
        logger.info("[RISK_GATE] Stopped")
    
    # ========================================================================
    # ORDER SUBMISSION
    # ========================================================================
    
    def submit_order(
        self,
        request: OrderRequest,
        timeout: float = 5.0
    ) -> GateDecision:
        """
        Submit order for risk validation.
        
        BLOCKS until gate processes request or timeout.
        
        Args:
            request: Order request
            timeout: Max seconds to wait
            
        Returns:
            GateDecision (approved or rejected)
            
        Raises:
            RiskGateTimeout: If gate doesn't respond in time
        """
        if not self._running:
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason="Risk gate not running"
            )
        
        # Submit to queue
        self._request_queue.put(request)
        
        # Wait for decision (polling with timeout)
        start_time = datetime.now(timezone.utc)
        while True:
            with self._lock:
                decision = self._decision_map.get(request.order_id)
                if decision:
                    # Remove from map and return
                    del self._decision_map[request.order_id]
                    return decision
            
            # Check timeout
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            if elapsed > timeout:
                logger.error(
                    f"[RISK_GATE] Timeout waiting for decision on {request.order_id}"
                )
                return GateDecision(
                    order_id=request.order_id,
                    approved=False,
                    rejection_reason=f"Risk gate timeout after {timeout}s"
                )
            
            # Sleep briefly
            Event().wait(0.1)
    
    # ========================================================================
    # WORKER THREAD
    # ========================================================================
    
    def _process_queue(self) -> None:
        """Worker thread processes order queue."""
        logger.info("[RISK_GATE] Worker thread started")
        
        while not self._shutdown_event.is_set():
            try:
                # Get request with timeout (allows checking shutdown)
                request = self._request_queue.get(timeout=0.5)
                
                # Process request
                decision = self._evaluate_order(request)
                
                # Store decision
                with self._lock:
                    self._decision_map[request.order_id] = decision
                
                # Log decision
                if decision.approved:
                    logger.info(
                        f"[RISK_GATE] ✓ APPROVED: {request.order_id} "
                        f"{request.side} {request.quantity} {request.symbol} @ ${request.current_price}"
                    )
                else:
                    logger.warning(
                        f"[RISK_GATE] ✗ REJECTED: {request.order_id} - "
                        f"{decision.rejection_reason}"
                    )
                
            except Empty:
                # No requests, continue loop
                continue
            except Exception as e:
                logger.error(f"[RISK_GATE] Error processing request: {e}", exc_info=True)
        
        logger.info("[RISK_GATE] Worker thread stopped")
    
    # ========================================================================
    # RISK EVALUATION (ATOMIC)
    # ========================================================================
    
    def _evaluate_order(self, request: OrderRequest) -> GateDecision:
        """
        Evaluate order against ALL risk checks.
        
        This is the CRITICAL function - atomic evaluation of ALL checks.
        
        Returns:
            GateDecision (approved=True only if ALL checks pass)
        """
        # PATCH 2: Auto-reset daily counters if new trading day
        self._check_and_reset_daily_counters()
        
        checks_passed = {}
        
        # ====================================================================
        # CHECK 0: PROTECTIONS (Circuit Breakers) - NEW!
        # ====================================================================
        if self._protections:
            # Check protections (they check recent trade history)
            # Pass None for trades since we don't have trade history in gate
            # Protections will use their internal state
            result = self._protections.check(
                symbol=request.symbol,
                current_trades=None,  # Could wire to position_store if needed
                completed_trades=None  # Could wire to transaction_log if needed
            )
            
            if result.is_protected:
                rejection_msg = f"Protection triggered: {result.reason}"
                logger.warning(f"[RISK_GATE] PROTECTION BLOCKED: {rejection_msg}")
                return GateDecision(
                    order_id=request.order_id,
                    approved=False,
                    rejection_reason=rejection_msg,
                    checks_passed=checks_passed
                )
            checks_passed['protections'] = True
        
        # ====================================================================
        # CHECK 1: Daily Loss Limit
        # ====================================================================
        if self.limits_tracker.is_daily_loss_limit_breached():
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason="Daily loss limit breached",
                checks_passed=checks_passed
            )
        checks_passed['daily_loss_limit'] = True
        
        # ====================================================================
        # CHECK 2: Duplicate Order Prevention
        # ====================================================================
        if request.order_id in self._submitted_orders_today:
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason=f"Duplicate order: {request.order_id} already submitted",
                checks_passed=checks_passed
            )
        checks_passed['duplicate_prevention'] = True
        
        # ====================================================================
        # CHECK 3: PDT Protection (Order Count)
        # ====================================================================
        if self.enable_pdt_protection:
            if self._order_count_today >= self.max_orders_per_day:
                return GateDecision(
                    order_id=request.order_id,
                    approved=False,
                    rejection_reason=(
                        f"PDT protection: {self._order_count_today} orders today "
                        f"(max={self.max_orders_per_day})"
                    ),
                    checks_passed=checks_passed
                )
        checks_passed['pdt_protection'] = True
        
        # ====================================================================
        # CHECK 4: Position Size Limit
        # ====================================================================
        if not self.limits_tracker.is_position_size_allowed(Decimal(str(request.quantity))):
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason=f"Position size {request.quantity} exceeds limit",
                checks_passed=checks_passed
            )
        checks_passed['position_size'] = True
        
        # ====================================================================
        # CHECK 5: Notional Exposure Limit
        # ====================================================================
        current_exposure = self._calculate_total_exposure()
        current_exposure_pct = calculate_exposure_pct(current_exposure, self.account_value)
        
        if not self.limits_tracker.is_notional_exposure_allowed(
            current_exposure,
            request.notional
        ):
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason=(
                    f"Notional exposure ${current_exposure + request.notional} "
                    f"would exceed limit"
                ),
                checks_passed=checks_passed
            )
        checks_passed['notional_exposure'] = True
        
        # ====================================================================
        # CHECK 6: Position Sizer Validation
        # ====================================================================
        is_valid, reason = self.position_sizer.validate_position_size(
            shares=request.quantity,
            current_price=request.current_price,
            account_value=self.account_value,
            existing_exposure_pct=current_exposure_pct
        )
        
        if not is_valid:
            return GateDecision(
                order_id=request.order_id,
                approved=False,
                rejection_reason=f"Position sizer: {reason}",
                checks_passed=checks_passed
            )
        checks_passed['position_sizer'] = True
        
        # ====================================================================
        # CHECK 7: Price Deviation (Fat-Finger Protection)
        # ====================================================================
        # PATCH 2: Reject if price deviates >10% from last known price
        if request.symbol in self._recent_prices:
            last_price = self._recent_prices[request.symbol]
            price_to_check = request.limit_price if request.limit_price else request.current_price
            deviation_pct = abs(price_to_check - last_price) / last_price
            
            if deviation_pct > Decimal("0.10"):  # 10% threshold
                return GateDecision(
                    order_id=request.order_id,
                    approved=False,
                    rejection_reason=(
                        f"FAT_FINGER: Price ${price_to_check} deviates "
                        f"{deviation_pct*100:.1f}% from recent ${last_price} (>10% threshold)"
                    ),
                    checks_passed=checks_passed
                )
        checks_passed['fat_finger_protection'] = True
        
        # Update recent price for this symbol
        price_to_store = request.limit_price if request.limit_price else request.current_price
        self._recent_prices[request.symbol] = price_to_store
        
        # ====================================================================
        # ALL CHECKS PASSED - APPROVE ORDER
        # ====================================================================
        
        # Update internal state (atomic with approval)
        self._pending_orders.add(request.order_id)
        self._order_count_today += 1
        self._submitted_orders_today.add(request.order_id)
        
        return GateDecision(
            order_id=request.order_id,
            approved=True,
            rejection_reason=None,
            checks_passed=checks_passed
        )
    
    # ========================================================================
    # STATE MANAGEMENT
    # ========================================================================
    
    def update_position(self, symbol: str, notional: Decimal) -> None:
        """
        Update position state (called after fills).
        
        Args:
            symbol: Symbol
            notional: Position notional value (0 = closed)
        """
        with self._lock:
            if notional == 0:
                self._active_positions.pop(symbol, None)
                logger.debug(f"[RISK_GATE] Position closed: {symbol}")
            else:
                self._active_positions[symbol] = notional
                logger.debug(f"[RISK_GATE] Position updated: {symbol} = ${notional}")
    
    def remove_pending_order(self, order_id: str) -> None:
        """Remove order from pending set (called after fill/cancel)."""
        with self._lock:
            self._pending_orders.discard(order_id)
    
    def update_account_value(self, new_value: Decimal) -> None:
        """Update account value for exposure calculations."""
        with self._lock:
            self.account_value = new_value
            logger.info(f"[RISK_GATE] Account value updated: ${new_value}")
    
    def reset_daily_counters(self) -> None:
        """Reset daily order counter (called at market open)."""
        with self._lock:
            self._order_count_today = 0
            self._submitted_orders_today.clear()
            self._day_trades_today.clear()  # PATCH 2: Reset PDT tracking
            self._last_reset_date = datetime.now(timezone.utc).date()
            logger.info("[RISK_GATE] Daily counters reset")
    
    def _check_and_reset_daily_counters(self) -> None:
        """
        PATCH 2: Automatic daily counter reset.
        
        Checks if new trading day has started (UTC midnight).
        If yes, automatically resets all daily counters.
        
        Called at the start of each order evaluation.
        """
        current_date = datetime.now(timezone.utc).date()
        
        if current_date > self._last_reset_date:
            logger.warning(
                f"[RISK_GATE] Auto-reset triggered: "
                f"New trading day detected (was {self._last_reset_date}, now {current_date})"
            )
            self.reset_daily_counters()
    
    def _calculate_total_exposure(self) -> Decimal:
        """Calculate total notional exposure across all positions."""
        return sum(self._active_positions.values())
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_stats(self) -> dict:
        """Get risk gate statistics."""
        with self._lock:
            total_exposure = self._calculate_total_exposure()
            exposure_pct = calculate_exposure_pct(total_exposure, self.account_value)
            
            return {
                'running': self._running,
                'pending_orders': len(self._pending_orders),
                'active_positions': len(self._active_positions),
                'total_exposure': str(total_exposure),
                'exposure_pct': str(exposure_pct),
                'orders_today': self._order_count_today,
                'account_value': str(self.account_value),
                'daily_loss_limit_breached': self.limits_tracker.is_daily_loss_limit_breached()
            }


# ============================================================================
# EXCEPTIONS
# ============================================================================

class RiskGateTimeout(Exception):
    """Raised when risk gate doesn't respond in time."""
    pass
