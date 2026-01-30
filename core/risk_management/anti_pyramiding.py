"""
Anti-pyramiding protection to prevent adding to losing positions.

ARCHITECTURE:
- Track position P&L in real-time
- Block additional entries when losing
- Allow scaling winners, prevent scaling losers
- Configurable loss thresholds

DESIGN PRINCIPLE:
Never average down on a losing position.

PYRAMIDING RULES:
- ALLOW: Adding to profitable positions
- BLOCK: Adding to losing positions beyond threshold
- ALLOW: First entry (no existing position)

EXAMPLE:
Position: LONG AAPL 100 shares @ $180, current $175 (-2.78%)
New Signal: BUY AAPL (pyramiding attempt)
Result: BLOCKED - position losing >2%, no pyramiding allowed

Based on institutional risk management best practices.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional, List
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# POSITION STATE
# ============================================================================

@dataclass
class PositionState:
    """Current state of a position."""
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_percent: Decimal
    entry_time: datetime
    
    def is_profitable(self) -> bool:
        """Check if position is profitable."""
        return self.unrealized_pnl > 0
    
    def is_losing(self) -> bool:
        """Check if position is losing."""
        return self.unrealized_pnl < 0


# ============================================================================
# PYRAMIDING DECISION
# ============================================================================

class PyramidingDecision(Enum):
    """Pyramiding decision result."""
    ALLOWED = "allowed"
    BLOCKED_LOSING = "blocked_losing"
    BLOCKED_THRESHOLD = "blocked_threshold"
    BLOCKED_MAX_SIZE = "blocked_max_size"


@dataclass
class PyramidingCheck:
    """Result of pyramiding check."""
    decision: PyramidingDecision
    reason: str
    current_pnl_percent: Decimal
    current_size: Decimal
    proposed_size: Decimal
    
    @property
    def allowed(self) -> bool:
        """Check if pyramiding is allowed."""
        return self.decision == PyramidingDecision.ALLOWED


# ============================================================================
# ANTI-PYRAMIDING GUARDIAN
# ============================================================================

class AntiPyramidingGuardian:
    """
    Prevent adding to losing positions (averaging down).
    
    RESPONSIBILITIES:
    - Track real-time position P&L
    - Block entries when position losing
    - Allow scaling profitable positions
    - Enforce maximum position size
    
    PYRAMIDING RULES:
    1. NO POSITION → Always allow (first entry)
    2. PROFITABLE POSITION → Allow adding (scaling winner)
    3. LOSING POSITION → Block adding (no averaging down)
    4. MAX SIZE REACHED → Block (regardless of P&L)
    
    THRESHOLDS:
    - Max loss to allow pyramiding: Configurable (default: 0%)
    - Max position size: % of portfolio
    - Min profit to allow pyramiding: Optional
    
    USAGE:
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0"),  # Block any losing add
            max_position_size_percent=Decimal("20.0"),   # Max 20% per position
            min_profit_to_pyramid_percent=Decimal("1.0") # Must be +1% to add
        )
        
        # Before adding to position
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("175.00"),
            portfolio_value=Decimal("50000")
        )
        
        if check.allowed:
            place_order()
        else:
            logger.warning(f"Pyramiding blocked: {check.reason}")
    """
    
    def __init__(
        self,
        max_pyramiding_loss_percent: Decimal = Decimal("0.0"),
        max_position_size_percent: Decimal = Decimal("20.0"),
        min_profit_to_pyramid_percent: Optional[Decimal] = None,
        allow_pyramiding_at_breakeven: bool = False
    ):
        """
        Initialize anti-pyramiding guardian.
        
        Args:
            max_pyramiding_loss_percent: Max loss % to allow pyramiding (0 = no losers)
            max_position_size_percent: Max position size as % of portfolio
            min_profit_to_pyramid_percent: Min profit % required to pyramid (optional)
            allow_pyramiding_at_breakeven: Allow adding at breakeven
        """
        self.max_pyramiding_loss = max_pyramiding_loss_percent
        self.max_position_size = max_position_size_percent
        self.min_profit_to_pyramid = min_profit_to_pyramid_percent
        self.allow_at_breakeven = allow_pyramiding_at_breakeven
        
        self.logger = get_logger(LogStream.RISK)
        
        # State tracking
        self.positions: Dict[str, PositionState] = {}
        self.blocked_pyramids: List[Dict] = []
        
        self.logger.info("AntiPyramidingGuardian initialized", extra={
            "max_pyramiding_loss": str(max_pyramiding_loss_percent),
            "max_position_size": str(max_position_size_percent),
            "min_profit_to_pyramid": str(min_profit_to_pyramid_percent) if min_profit_to_pyramid_percent else None
        })
    
    # ========================================================================
    # PYRAMIDING CHECKS
    # ========================================================================
    
    def check_pyramiding(
        self,
        symbol: str,
        side: str,
        current_position_size: Decimal,
        proposed_add_size: Decimal,
        avg_entry_price: Decimal,
        current_price: Decimal,
        portfolio_value: Decimal
    ) -> PyramidingCheck:
        """
        Check if pyramiding (adding to position) is allowed.
        
        Args:
            symbol: Stock symbol
            side: "LONG" or "SHORT"
            current_position_size: Current position notional value
            proposed_add_size: Proposed additional notional value
            avg_entry_price: Average entry price of current position
            current_price: Current market price
            portfolio_value: Total portfolio value
            
        Returns:
            PyramidingCheck with decision and reasoning
        """
        # No position → Always allow (first entry)
        if current_position_size == 0:
            return PyramidingCheck(
                decision=PyramidingDecision.ALLOWED,
                reason="No existing position - first entry allowed",
                current_pnl_percent=Decimal("0"),
                current_size=Decimal("0"),
                proposed_size=proposed_add_size
            )
        
        # Calculate current P&L
        if side == "LONG":
            pnl_percent = (current_price - avg_entry_price) / avg_entry_price * Decimal("100")
        else:  # SHORT
            pnl_percent = (avg_entry_price - current_price) / avg_entry_price * Decimal("100")
        
        # Check if position is losing beyond threshold
        if pnl_percent < -abs(self.max_pyramiding_loss):
            self._log_blocked_pyramid(symbol, side, pnl_percent, "losing_threshold")
            
            return PyramidingCheck(
                decision=PyramidingDecision.BLOCKED_LOSING,
                reason=f"Position losing {pnl_percent:.2f}%, max allowed: -{self.max_pyramiding_loss}%",
                current_pnl_percent=pnl_percent,
                current_size=current_position_size,
                proposed_size=proposed_add_size
            )
        
        # Check if at breakeven and not allowed
        if pnl_percent == 0 and not self.allow_at_breakeven:
            self._log_blocked_pyramid(symbol, side, pnl_percent, "breakeven")
            
            return PyramidingCheck(
                decision=PyramidingDecision.BLOCKED_THRESHOLD,
                reason="Position at breakeven, pyramiding not allowed",
                current_pnl_percent=pnl_percent,
                current_size=current_position_size,
                proposed_size=proposed_add_size
            )
        
        # Check minimum profit requirement
        if self.min_profit_to_pyramid is not None:
            if pnl_percent < self.min_profit_to_pyramid:
                self._log_blocked_pyramid(symbol, side, pnl_percent, "min_profit")
                
                return PyramidingCheck(
                    decision=PyramidingDecision.BLOCKED_THRESHOLD,
                    reason=f"Position profit {pnl_percent:.2f}%, min required: {self.min_profit_to_pyramid}%",
                    current_pnl_percent=pnl_percent,
                    current_size=current_position_size,
                    proposed_size=proposed_add_size
                )
        
        # Check maximum position size
        total_size = current_position_size + proposed_add_size
        size_percent = (total_size / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")
        
        if size_percent > self.max_position_size:
            self._log_blocked_pyramid(symbol, side, pnl_percent, "max_size")
            
            return PyramidingCheck(
                decision=PyramidingDecision.BLOCKED_MAX_SIZE,
                reason=f"Total size {size_percent:.1f}% exceeds max {self.max_position_size}%",
                current_pnl_percent=pnl_percent,
                current_size=current_position_size,
                proposed_size=proposed_add_size
            )
        
        # All checks passed - allow pyramiding
        self.logger.info(f"Pyramiding allowed: {symbol}", extra={
            "side": side,
            "current_pnl_percent": str(pnl_percent),
            "current_size": str(current_position_size),
            "add_size": str(proposed_add_size)
        })
        
        return PyramidingCheck(
            decision=PyramidingDecision.ALLOWED,
            reason=f"Position profitable ({pnl_percent:.2f}%), pyramiding allowed",
            current_pnl_percent=pnl_percent,
            current_size=current_position_size,
            proposed_size=proposed_add_size
        )
    
    # ========================================================================
    # POSITION TRACKING
    # ========================================================================
    
    def update_position(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        avg_entry_price: Decimal,
        current_price: Decimal,
        entry_time: Optional[datetime] = None
    ):
        """Update position state for tracking."""
        entry_time = entry_time or datetime.now(timezone.utc)
        
        # Calculate P&L
        if side == "LONG":
            unrealized_pnl = (current_price - avg_entry_price) * quantity
            unrealized_pnl_percent = (current_price - avg_entry_price) / avg_entry_price * Decimal("100")
        else:  # SHORT
            unrealized_pnl = (avg_entry_price - current_price) * quantity
            unrealized_pnl_percent = (avg_entry_price - current_price) / avg_entry_price * Decimal("100")
        
        self.positions[symbol] = PositionState(
            symbol=symbol,
            side=side,
            quantity=quantity,
            avg_entry_price=avg_entry_price,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_percent=unrealized_pnl_percent,
            entry_time=entry_time
        )
    
    def remove_position(self, symbol: str):
        """Remove position when closed."""
        if symbol in self.positions:
            del self.positions[symbol]
    
    def get_position(self, symbol: str) -> Optional[PositionState]:
        """Get current position state."""
        return self.positions.get(symbol)
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def _log_blocked_pyramid(
        self,
        symbol: str,
        side: str,
        pnl_percent: Decimal,
        reason: str
    ):
        """Log a blocked pyramiding attempt."""
        self.logger.warning(f"Pyramiding BLOCKED: {symbol}", extra={
            "symbol": symbol,
            "side": side,
            "pnl_percent": str(pnl_percent),
            "reason": reason
        })
        
        self.blocked_pyramids.append({
            "timestamp": datetime.now(timezone.utc),
            "symbol": symbol,
            "side": side,
            "pnl_percent": pnl_percent,
            "reason": reason
        })
    
    def get_blocked_pyramid_count(self) -> int:
        """Get count of blocked pyramiding attempts."""
        return len(self.blocked_pyramids)
    
    def get_statistics(self) -> Dict:
        """Get anti-pyramiding statistics."""
        return {
            "active_positions": len(self.positions),
            "blocked_pyramids": len(self.blocked_pyramids),
            "config": {
                "max_pyramiding_loss": str(self.max_pyramiding_loss),
                "max_position_size": str(self.max_position_size),
                "min_profit_to_pyramid": str(self.min_profit_to_pyramid) if self.min_profit_to_pyramid else None,
                "allow_at_breakeven": self.allow_at_breakeven
            },
            "current_positions": {
                symbol: {
                    "side": pos.side,
                    "pnl_percent": str(pos.unrealized_pnl_percent),
                    "is_profitable": pos.is_profitable()
                }
                for symbol, pos in self.positions.items()
            }
        }
