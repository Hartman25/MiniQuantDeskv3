"""
Dynamic position sizing based on volatility.

ARCHITECTURE:
- ATR-based risk calculation
- Volatility-adjusted position sizing
- Per-symbol risk limits
- Account-level risk constraints
- Kelly Criterion support (optional)

DESIGN PRINCIPLE:
Equal risk per trade, not equal dollars.

EXAMPLE:
- SPY: ATR=$5, risk_per_trade=$100 → 20 shares
- TSLA: ATR=$20, risk_per_trade=$100 → 5 shares

This ensures we don't risk more on volatile stocks.

Based on Van Tharp's position sizing and LEAN's PortfolioConstruction.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Optional, Tuple
from enum import Enum
import math

from core.logging import get_logger, LogStream


# ============================================================================
# SIZING METHODS
# ============================================================================

class SizingMethod(Enum):
    """Position sizing method."""
    FIXED_DOLLAR = "FIXED_DOLLAR"  # Fixed dollar amount
    FIXED_PERCENT = "FIXED_PERCENT"  # Fixed % of account
    VOLATILITY_ADJUSTED = "VOLATILITY_ADJUSTED"  # ATR-based
    KELLY = "KELLY"  # Kelly Criterion (experimental)


# ============================================================================
# POSITION SIZE RESULT
# ============================================================================

@dataclass
class PositionSizeResult:
    """Result of position size calculation."""
    symbol: str
    suggested_shares: int
    max_shares: int
    suggested_notional: Decimal
    max_notional: Decimal
    risk_per_share: Decimal
    risk_dollars: Decimal
    sizing_method: SizingMethod
    constraints_applied: list
    metadata: Dict
    
    def is_constrained(self) -> bool:
        """Check if position was constrained."""
        return len(self.constraints_applied) > 0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "suggested_shares": self.suggested_shares,
            "max_shares": self.max_shares,
            "suggested_notional": str(self.suggested_notional),
            "max_notional": str(self.max_notional),
            "risk_per_share": str(self.risk_per_share),
            "risk_dollars": str(self.risk_dollars),
            "sizing_method": self.sizing_method.value,
            "constraints_applied": self.constraints_applied,
            "metadata": self.metadata
        }


# ============================================================================
# DYNAMIC POSITION SIZER
# ============================================================================

class DynamicPositionSizer:
    """
    Dynamic position sizing with volatility adjustment.
    
    RESPONSIBILITIES:
    - Calculate position sizes based on ATR
    - Apply account-level constraints
    - Apply per-symbol constraints
    - Support multiple sizing methods
    - Track sizing history
    
    SIZING LOGIC:
    1. Calculate base size (from method)
    2. Apply volatility adjustment (if enabled)
    3. Apply max position size constraint
    4. Apply max notional constraint
    5. Apply available buying power constraint
    6. Round to whole shares
    
    USAGE:
        sizer = DynamicPositionSizer(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0"),  # 1% risk per trade
            max_position_size_percent=Decimal("10.0"),  # Max 10% per position
            sizing_method=SizingMethod.VOLATILITY_ADJUSTED
        )
        
        # Calculate size
        result = sizer.calculate_size(
            symbol="SPY",
            current_price=Decimal("600.00"),
            atr=Decimal("5.00")  # ATR for volatility
        )
        
        print(f"Buy {result.suggested_shares} shares")
        print(f"Risk: ${result.risk_dollars}")
    """
    
    def __init__(
        self,
        account_equity: Decimal,
        risk_per_trade_percent: Decimal = Decimal("1.0"),
        max_position_size_percent: Decimal = Decimal("10.0"),
        max_notional: Optional[Decimal] = None,
        sizing_method: SizingMethod = SizingMethod.VOLATILITY_ADJUSTED,
        min_shares: int = 1,
        volatility_multiplier: Decimal = Decimal("2.0")  # Risk = ATR * multiplier
    ):
        """
        Initialize position sizer.
        
        Args:
            account_equity: Current account equity
            risk_per_trade_percent: % of account to risk per trade (e.g., 1.0 = 1%)
            max_position_size_percent: Max % of account per position (e.g., 10.0 = 10%)
            max_notional: Max notional dollar value per position
            sizing_method: Sizing method to use
            min_shares: Minimum shares to trade
            volatility_multiplier: Multiplier for ATR to calculate risk
                                   (e.g., 2.0 = stop at 2*ATR)
        """
        self.account_equity = account_equity
        self.risk_per_trade_percent = risk_per_trade_percent
        self.max_position_size_percent = max_position_size_percent
        self.max_notional = max_notional
        self.sizing_method = sizing_method
        self.min_shares = min_shares
        self.volatility_multiplier = volatility_multiplier
        
        self.logger = get_logger(LogStream.RISK)
        
        # Calculate derived values
        self.risk_per_trade_dollars = (account_equity * risk_per_trade_percent) / Decimal("100")
        self.max_position_notional = (account_equity * max_position_size_percent) / Decimal("100")
        
        if max_notional:
            self.max_position_notional = min(self.max_position_notional, max_notional)
        
        self.logger.info("DynamicPositionSizer initialized", extra={
            "account_equity": str(account_equity),
            "risk_per_trade_dollars": str(self.risk_per_trade_dollars),
            "max_position_notional": str(self.max_position_notional),
            "sizing_method": sizing_method.value
        })
    
    # ========================================================================
    # POSITION SIZE CALCULATION
    # ========================================================================
    
    def calculate_size(
        self,
        symbol: str,
        current_price: Decimal,
        atr: Optional[Decimal] = None,
        win_rate: Optional[float] = None,
        avg_win_loss_ratio: Optional[float] = None,
        available_buying_power: Optional[Decimal] = None
    ) -> PositionSizeResult:
        """
        Calculate position size for a symbol.
        
        Args:
            symbol: Stock symbol
            current_price: Current market price
            atr: Average True Range (for volatility-adjusted sizing)
            win_rate: Win rate (for Kelly criterion)
            avg_win_loss_ratio: Average win/loss ratio (for Kelly)
            available_buying_power: Available buying power
            
        Returns:
            PositionSizeResult with suggested size and constraints
        """
        constraints_applied = []
        metadata = {}
        
        # Step 1: Calculate base size using selected method
        if self.sizing_method == SizingMethod.VOLATILITY_ADJUSTED:
            if atr is None:
                raise ValueError(f"ATR required for VOLATILITY_ADJUSTED sizing: {symbol}")
            
            base_shares = self._calculate_volatility_adjusted_size(
                current_price, atr
            )
            metadata["atr"] = str(atr)
            metadata["volatility_multiplier"] = str(self.volatility_multiplier)
        
        elif self.sizing_method == SizingMethod.FIXED_PERCENT:
            base_shares = self._calculate_fixed_percent_size(current_price)
        
        elif self.sizing_method == SizingMethod.FIXED_DOLLAR:
            base_shares = self._calculate_fixed_dollar_size(current_price)
        
        elif self.sizing_method == SizingMethod.KELLY:
            if win_rate is None or avg_win_loss_ratio is None:
                raise ValueError("win_rate and avg_win_loss_ratio required for KELLY")
            
            base_shares = self._calculate_kelly_size(
                current_price, win_rate, avg_win_loss_ratio
            )
            metadata["win_rate"] = win_rate
            metadata["avg_win_loss_ratio"] = avg_win_loss_ratio
        
        else:
            raise ValueError(f"Unknown sizing method: {self.sizing_method}")
        
        # Step 2: Apply constraints
        constrained_shares = base_shares
        
        # Minimum shares constraint
        if constrained_shares < self.min_shares:
            constrained_shares = self.min_shares
            constraints_applied.append(f"min_shares ({self.min_shares})")
        
        # Maximum position notional constraint
        max_shares_notional = int(self.max_position_notional / current_price)
        if constrained_shares > max_shares_notional:
            constrained_shares = max_shares_notional
            constraints_applied.append(
                f"max_position_notional ({self.max_position_size_percent}%)"
            )
        
        # Buying power constraint
        if available_buying_power is not None:
            max_shares_bp = int(available_buying_power / current_price)
            if constrained_shares > max_shares_bp:
                constrained_shares = max_shares_bp
                constraints_applied.append("buying_power")
        
        # Step 3: Calculate risk metrics
        if atr is not None:
            risk_per_share = atr * self.volatility_multiplier
        else:
            # Estimate risk as % of price
            risk_per_share = current_price * Decimal("0.02")  # 2% default
        
        risk_dollars = risk_per_share * Decimal(str(constrained_shares))
        
        # Step 4: Create result
        result = PositionSizeResult(
            symbol=symbol,
            suggested_shares=constrained_shares,
            max_shares=max_shares_notional,
            suggested_notional=current_price * Decimal(str(constrained_shares)),
            max_notional=self.max_position_notional,
            risk_per_share=risk_per_share,
            risk_dollars=risk_dollars,
            sizing_method=self.sizing_method,
            constraints_applied=constraints_applied,
            metadata=metadata
        )
        
        self.logger.debug(
            f"Calculated position size: {symbol}",
            extra=result.to_dict()
        )
        
        return result
    
    # ========================================================================
    # SIZING METHOD IMPLEMENTATIONS
    # ========================================================================
    
    def _calculate_volatility_adjusted_size(
        self,
        current_price: Decimal,
        atr: Decimal
    ) -> int:
        """
        Calculate size using ATR-based volatility adjustment.
        
        Formula:
            risk_per_share = ATR * volatility_multiplier
            shares = risk_dollars / risk_per_share
        
        This ensures equal risk across all positions regardless of volatility.
        """
        risk_per_share = atr * self.volatility_multiplier
        
        if risk_per_share <= 0:
            # Fallback to fixed percent
            return self._calculate_fixed_percent_size(current_price)
        
        shares = self.risk_per_trade_dollars / risk_per_share
        return int(shares)
    
    def _calculate_fixed_percent_size(self, current_price: Decimal) -> int:
        """Calculate size as fixed % of account."""
        notional = self.max_position_notional
        shares = notional / current_price
        return int(shares)
    
    def _calculate_fixed_dollar_size(self, current_price: Decimal) -> int:
        """Calculate size as fixed dollar amount."""
        # Use risk_per_trade_dollars as fixed dollar amount
        shares = self.risk_per_trade_dollars / current_price
        return int(shares)
    
    def _calculate_kelly_size(
        self,
        current_price: Decimal,
        win_rate: float,
        avg_win_loss_ratio: float
    ) -> int:
        """
        Calculate size using Kelly Criterion.
        
        Formula:
            kelly_fraction = win_rate - ((1 - win_rate) / avg_win_loss_ratio)
            size = account_equity * kelly_fraction / current_price
        
        WARNING: Kelly can be aggressive. Consider using fractional Kelly (e.g., 0.5 * kelly).
        """
        # Kelly fraction
        kelly = win_rate - ((1 - win_rate) / avg_win_loss_ratio)
        
        # Cap at max position size
        kelly = min(kelly, float(self.max_position_size_percent) / 100)
        
        # Ensure non-negative
        kelly = max(kelly, 0.0)
        
        # Calculate shares
        notional = self.account_equity * Decimal(str(kelly))
        shares = notional / current_price
        
        return int(shares)
    
    # ========================================================================
    # ACCOUNT UPDATE
    # ========================================================================
    
    def update_account_equity(self, new_equity: Decimal):
        """Update account equity (call daily or on significant changes)."""
        self.account_equity = new_equity
        self.risk_per_trade_dollars = (new_equity * self.risk_per_trade_percent) / Decimal("100")
        self.max_position_notional = (new_equity * self.max_position_size_percent) / Decimal("100")
        
        if self.max_notional:
            self.max_position_notional = min(self.max_position_notional, self.max_notional)
        
        self.logger.info("Updated account equity", extra={
            "new_equity": str(new_equity),
            "risk_per_trade_dollars": str(self.risk_per_trade_dollars),
            "max_position_notional": str(self.max_position_notional)
        })
