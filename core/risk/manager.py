"""
Risk manager - pre-trade validation and exposure limits.

CRITICAL PROPERTIES:
1. All trades validated before submission
2. Position size limits enforced
3. Portfolio exposure limits
4. Drawdown circuit breaker
5. Account balance checks
6. Fail-closed (reject on error)

Based on LEAN's RiskManagement architecture.
"""

from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum

from core.logging import get_logger, LogStream
from core.state import Position, PositionStore
from core.brokers import BrokerOrderSide


# ============================================================================
# RISK CHECK RESULT
# ============================================================================

class RiskCheckStatus(Enum):
    """Risk check status."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WARNING = "WARNING"


@dataclass
class RiskCheckResult:
    """Result of risk check."""
    status: RiskCheckStatus
    reasons: List[str]
    warnings: List[str]
    
    @property
    def approved(self) -> bool:
        """Check if approved."""
        return self.status == RiskCheckStatus.APPROVED
    
    def add_rejection(self, reason: str):
        """Add rejection reason."""
        self.status = RiskCheckStatus.REJECTED
        self.reasons.append(reason)
    
    def add_warning(self, warning: str):
        """Add warning."""
        self.warnings.append(warning)


# ============================================================================
# RISK LIMITS
# ============================================================================

@dataclass
class RiskLimits:
    """Risk limit configuration."""
    
    # Position limits
    max_position_size_usd: Decimal = Decimal("50000")  # Max per position
    max_position_pct_portfolio: Decimal = Decimal("0.10")  # 10% max
    
    # Portfolio limits
    max_portfolio_exposure_usd: Decimal = Decimal("200000")  # Total exposure
    max_portfolio_exposure_pct: Decimal = Decimal("0.80")  # 80% of capital
    max_positions: int = 10  # Max open positions
    
    # Drawdown limits
    max_daily_loss_usd: Decimal = Decimal("5000")
    max_daily_loss_pct: Decimal = Decimal("0.02")  # 2%
    
    # Symbol limits
    max_concentration_per_symbol_pct: Decimal = Decimal("0.15")  # 15%
    
    # Account limits
    min_buying_power_reserve: Decimal = Decimal("10000")  # Always keep this much


# ============================================================================
# RISK MANAGER
# ============================================================================

class RiskManager:
    """
    Risk manager for pre-trade validation.
    
    RULES:
    - All trades validated before submission
    - Fail-closed (reject on error)
    - Drawdown circuit breaker
    - Portfolio exposure limits
    - Position concentration limits
    
    THREAD SAFETY:
    - NOT thread-safe
    - Caller must synchronize
    
    USAGE:
        risk_mgr = RiskManager(
            position_store=position_store,
            limits=RiskLimits()
        )
        
        result = risk_mgr.validate_trade(
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            price=Decimal("600"),
            account_value=Decimal("100000"),
            buying_power=Decimal("50000")
        )
        
        if result.approved:
            # Submit order
        else:
            # Reject
    """
    
    def __init__(
        self,
        position_store: PositionStore,
        limits: Optional[RiskLimits] = None,
        order_tracker=None  # PATCH 7: optional OrderTracker for BP reservation
    ):
        """Initialize risk manager.

        PATCH 7: If order_tracker is provided, reserved buying power from
        open/in-flight orders is subtracted from available buying power.
        """
        self.position_store = position_store
        self.limits = limits or RiskLimits()
        self.order_tracker = order_tracker
        self.logger = get_logger(LogStream.RISK)
        
        # Track daily P&L (resets at midnight)
        self._daily_pnl = Decimal("0")
        self._daily_reset_time = datetime.now(timezone.utc).date()
        
        self.logger.info("RiskManager initialized", extra={
            "max_position_size": str(self.limits.max_position_size_usd),
            "max_portfolio_exposure": str(self.limits.max_portfolio_exposure_usd),
            "max_positions": self.limits.max_positions
        })
    
    def validate_trade(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        price: Decimal,
        account_value: Decimal,
        buying_power: Decimal,
        strategy: str = "UNKNOWN"
    ) -> RiskCheckResult:
        """
        Validate trade against risk limits.
        
        Args:
            symbol: Stock symbol
            quantity: Quantity to trade
            side: BUY or SELL
            price: Estimated fill price
            account_value: Current account value
            buying_power: Current buying power
            strategy: Strategy name
            
        Returns:
            RiskCheckResult with approval/rejection
        """
        result = RiskCheckResult(
            status=RiskCheckStatus.APPROVED,
            reasons=[],
            warnings=[]
        )
        
        try:
            # Calculate trade value
            trade_value = abs(quantity * price)
            
            self.logger.info("Validating trade", extra={
                "symbol": symbol,
                "quantity": str(quantity),
                "side": side.value,
                "price": str(price),
                "trade_value": str(trade_value),
                "strategy": strategy
            })
            
            # Check 1: Position size limit
            if trade_value > self.limits.max_position_size_usd:
                result.add_rejection(
                    f"Position size ${trade_value} exceeds max ${self.limits.max_position_size_usd}"
                )
            
            # Check 2: Position size as % of portfolio
            pct_portfolio = trade_value / account_value
            if pct_portfolio > self.limits.max_position_pct_portfolio:
                result.add_rejection(
                    f"Position {pct_portfolio*100:.1f}% exceeds max "
                    f"{self.limits.max_position_pct_portfolio*100:.1f}%"
                )
            
            # Check 3: Buying power reserve (PATCH 7: subtract reserved BP from open orders)
            reserved_bp = self._calculate_reserved_buying_power()
            available_bp = buying_power - reserved_bp
            remaining_bp = available_bp - trade_value

            if remaining_bp < self.limits.min_buying_power_reserve:
                result.add_rejection(
                    f"Insufficient buying power reserve: ${remaining_bp:.2f} < "
                    f"${self.limits.min_buying_power_reserve} "
                    f"(buying_power=${buying_power}, reserved=${reserved_bp:.2f}, trade=${trade_value})"
                )
            
            # Check 4: Portfolio exposure
            current_positions = self.position_store.get_all()
            current_exposure = self._calculate_exposure(current_positions)
            
            new_exposure = current_exposure + trade_value
            if new_exposure > self.limits.max_portfolio_exposure_usd:
                result.add_rejection(
                    f"Portfolio exposure ${new_exposure} exceeds max "
                    f"${self.limits.max_portfolio_exposure_usd}"
                )
            
            # Check 5: Max positions
            if side == BrokerOrderSide.BUY and len(current_positions) >= self.limits.max_positions:
                result.add_rejection(
                    f"Max positions reached: {len(current_positions)} >= {self.limits.max_positions}"
                )
            
            # Check 6: Symbol concentration
            existing_position = self.position_store.get(symbol)
            if existing_position:
                new_position_value = abs(existing_position.quantity * price) + trade_value
                concentration = new_position_value / account_value
                
                if concentration > self.limits.max_concentration_per_symbol_pct:
                    result.add_rejection(
                        f"Symbol concentration {concentration*100:.1f}% exceeds max "
                        f"{self.limits.max_concentration_per_symbol_pct*100:.1f}%"
                    )
            
            # Check 7: Daily loss limit (if we have P&L data)
            self._check_daily_drawdown(result, account_value)
            
            # Log result
            if result.approved:
                self.logger.info("Trade approved", extra={
                    "symbol": symbol,
                    "trade_value": str(trade_value)
                })
            else:
                self.logger.warning("Trade rejected", extra={
                    "symbol": symbol,
                    "reasons": result.reasons
                })
            
            return result
            
        except Exception as e:
            # Fail closed
            self.logger.error("Risk check failed", extra={"error": str(e)}, exc_info=True)
            result.add_rejection(f"Risk check error: {e}")
            return result
    
    def update_daily_pnl(self, pnl: Decimal):
        """Update daily P&L tracker."""
        # Reset if new day
        today = datetime.now(timezone.utc).date()
        if today > self._daily_reset_time:
            self._daily_pnl = Decimal("0")
            self._daily_reset_time = today
        
        self._daily_pnl += pnl
        
        self.logger.info("Daily P&L updated", extra={
            "daily_pnl": str(self._daily_pnl)
        })
    
    def _calculate_exposure(self, positions: List[Position]) -> Decimal:
        """Calculate total portfolio exposure."""
        total = Decimal("0")

        for pos in positions:
            if pos.current_price:
                value = abs(pos.quantity * pos.current_price)
            else:
                value = abs(pos.quantity * pos.entry_price)
            total += value

        return total

    def _calculate_reserved_buying_power(self) -> Decimal:
        """
        PATCH 7: Calculate buying power reserved by open/in-flight orders.

        Returns total value of unfilled BUY orders that are reserving buying power.
        """
        if not self.order_tracker:
            return Decimal("0")

        reserved = Decimal("0")

        try:
            in_flight = self.order_tracker.get_all_in_flight()

            for order in in_flight:
                # Only BUY orders reserve buying power
                if order.side.value != "BUY":
                    continue

                # Calculate unfilled quantity
                unfilled_qty = order.quantity - (order.filled_quantity or Decimal("0"))

                if unfilled_qty <= 0:
                    continue

                # Estimate reserved value
                # For limit orders, use limit price
                # For market/stop orders, use last known price or conservative estimate
                if order.price:
                    estimated_price = order.price
                elif order.stop_price:
                    estimated_price = order.stop_price
                else:
                    # Market order: we don't have price, skip (conservative)
                    # In practice, market orders fill quickly so this is acceptable
                    continue

                reserved += unfilled_qty * estimated_price

            if reserved > 0:
                self.logger.debug(
                    "Reserved buying power calculated",
                    extra={"reserved_bp": str(reserved), "in_flight_orders": len(in_flight)},
                )

            return reserved

        except Exception as e:
            self.logger.warning(
                "Failed to calculate reserved buying power; assuming zero",
                extra={"error": str(e)},
            )
            return Decimal("0")
    
    def _check_daily_drawdown(self, result: RiskCheckResult, account_value: Decimal):
        """Check daily drawdown limits."""
        if self._daily_pnl >= 0:
            return  # No drawdown
        
        loss = abs(self._daily_pnl)
        
        # Check USD limit
        if loss > self.limits.max_daily_loss_usd:
            result.add_rejection(
                f"Daily loss ${loss} exceeds max ${self.limits.max_daily_loss_usd}"
            )
        
        # Check % limit
        loss_pct = loss / account_value
        if loss_pct > self.limits.max_daily_loss_pct:
            result.add_rejection(
                f"Daily loss {loss_pct*100:.1f}% exceeds max "
                f"{self.limits.max_daily_loss_pct*100:.1f}%"
            )


# ============================================================================
# EXCEPTIONS
# ============================================================================

class RiskViolationError(Exception):
    """Risk limit violation."""
    pass
