"""
Notional Position Sizer - Prevents overexposure on small accounts.

CRITICAL PROBLEM SOLVED:
$200 account should NOT attempt $600 SPY trade (300% exposure).

SIZING RULES:
1. Position size = f(account_value, max_exposure_pct, current_price)
2. Account for existing positions (don't double-count)
3. Minimum position = 1 share (or 0 if can't afford)
4. Integer shares only (no fractional)

Based on QuantConnect's PositionSizer with notional limits.
"""

from decimal import Decimal, ROUND_DOWN
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# NOTIONAL POSITION SIZER
# ============================================================================

class NotionalPositionSizer:
    """
    Calculate position sizes based on notional exposure limits.
    
    INVARIANTS:
    - Never exceed max_exposure_per_position
    - Never exceed remaining account buying power
    - Account for existing positions
    - Return integer shares only
    
    Example:
        sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("0.10")  # 10% per position
        )
        
        shares = sizer.calculate_position_size(
            account_value=Decimal("200.00"),
            current_price=Decimal("580.00"),
            existing_exposure=Decimal("0.00")
        )
        # Returns 0 (can't afford 10% of $200 = $20 at $580/share)
    """
    
    def __init__(
        self,
        max_exposure_per_position: Decimal = Decimal("0.10"),  # 10% default
        max_total_exposure: Decimal = Decimal("0.95"),  # 95% max portfolio
        min_position_value: Decimal = Decimal("10.00")  # Minimum $ position
    ):
        """
        Initialize position sizer.
        
        Args:
            max_exposure_per_position: Max % of account per position (0.0 to 1.0)
            max_total_exposure: Max % of account across all positions
            min_position_value: Minimum $ value for a position
        """
        if not (0 < max_exposure_per_position <= 1):
            raise ValueError("max_exposure_per_position must be between 0 and 1")
        if not (0 < max_total_exposure <= 1):
            raise ValueError("max_total_exposure must be between 0 and 1")
        if max_exposure_per_position > max_total_exposure:
            raise ValueError("max_exposure_per_position cannot exceed max_total_exposure")
        
        self.max_exposure_per_position = max_exposure_per_position
        self.max_total_exposure = max_total_exposure
        self.min_position_value = min_position_value
        
        logger.info(
            f"NotionalPositionSizer initialized "
            f"(max_per_position={max_exposure_per_position:.1%}, "
            f"max_total={max_total_exposure:.1%}, "
            f"min_value=${min_position_value})"
        )
    
    def calculate_position_size(
        self,
        account_value: Decimal,
        current_price: Decimal,
        existing_exposure_pct: Decimal = Decimal("0"),
        side: str = "LONG"
    ) -> int:
        """
        Calculate position size in shares.
        
        Args:
            account_value: Current account equity
            current_price: Current price per share
            existing_exposure_pct: % of account already exposed (0.0 to 1.0)
            side: "LONG" or "SHORT"
            
        Returns:
            Integer number of shares (0 if can't afford minimum)
        """
        if account_value <= 0:
            logger.warning("Account value <= 0, returning 0 shares")
            return 0
        
        if current_price <= 0:
            logger.warning(f"Invalid price {current_price}, returning 0 shares")
            return 0
        
        # Check if we have room for new position
        remaining_exposure = self.max_total_exposure - existing_exposure_pct
        if remaining_exposure <= 0:
            logger.warning(
                f"No remaining exposure capacity "
                f"(existing={existing_exposure_pct:.1%}, "
                f"max={self.max_total_exposure:.1%})"
            )
            return 0
        
        # Calculate target notional value
        target_exposure_pct = min(
            self.max_exposure_per_position,
            remaining_exposure
        )
        target_notional = account_value * target_exposure_pct
        
        # Check minimum position value
        if target_notional < self.min_position_value:
            logger.warning(
                f"Target notional ${target_notional} < minimum ${self.min_position_value}, "
                f"returning 0 shares"
            )
            return 0
        
        # Calculate shares (round down to integer)
        shares_decimal = target_notional / current_price
        shares = int(shares_decimal.quantize(Decimal('1'), rounding=ROUND_DOWN))
        
        # Final validation
        if shares < 1:
            logger.info(
                f"Calculated {shares_decimal} shares, rounds to 0 "
                f"(price=${current_price} too high for ${target_notional} target)"
            )
            return 0
        
        # Calculate actual notional
        actual_notional = shares * current_price
        actual_exposure_pct = actual_notional / account_value
        
        logger.info(
            f"[SIZING] {side} {shares} shares @ ${current_price} = "
            f"${actual_notional} ({actual_exposure_pct:.2%} of ${account_value})"
        )
        
        return shares
    
    def validate_position_size(
        self,
        shares: int,
        current_price: Decimal,
        account_value: Decimal,
        existing_exposure_pct: Decimal = Decimal("0")
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a proposed position size.
        
        Args:
            shares: Proposed number of shares
            current_price: Current price per share
            account_value: Current account equity
            existing_exposure_pct: % of account already exposed
            
        Returns:
            (is_valid, rejection_reason)
        """
        if shares <= 0:
            return False, "Position size must be positive"
        
        # Calculate notional and exposure
        notional = shares * current_price
        exposure_pct = notional / account_value
        
        # Check per-position limit
        if exposure_pct > self.max_exposure_per_position:
            return False, (
                f"Exposure {exposure_pct:.2%} exceeds per-position limit "
                f"{self.max_exposure_per_position:.2%}"
            )
        
        # Check total exposure limit
        total_exposure = existing_exposure_pct + exposure_pct
        if total_exposure > self.max_total_exposure:
            return False, (
                f"Total exposure {total_exposure:.2%} would exceed limit "
                f"{self.max_total_exposure:.2%}"
            )
        
        # Check minimum position value
        if notional < self.min_position_value:
            return False, (
                f"Position value ${notional} below minimum ${self.min_position_value}"
            )
        
        return True, None
    
    def get_max_shares_affordable(
        self,
        account_value: Decimal,
        current_price: Decimal,
        existing_exposure_pct: Decimal = Decimal("0")
    ) -> int:
        """
        Calculate maximum shares affordable given constraints.
        
        Args:
            account_value: Current account equity
            current_price: Current price per share
            existing_exposure_pct: % of account already exposed
            
        Returns:
            Maximum integer shares
        """
        # Calculate remaining buying power
        remaining_exposure = self.max_total_exposure - existing_exposure_pct
        if remaining_exposure <= 0:
            return 0
        
        # Limit to per-position max
        available_exposure = min(remaining_exposure, self.max_exposure_per_position)
        available_notional = account_value * available_exposure
        
        # Calculate max shares
        max_shares_decimal = available_notional / current_price
        max_shares = int(max_shares_decimal.quantize(Decimal('1'), rounding=ROUND_DOWN))
        
        return max(max_shares, 0)
    
    def calculate_required_capital(
        self,
        shares: int,
        current_price: Decimal,
        margin_multiplier: Decimal = Decimal("1.0")
    ) -> Decimal:
        """
        Calculate capital required for position.
        
        Args:
            shares: Number of shares
            current_price: Price per share
            margin_multiplier: Margin requirement (1.0 = no margin, 0.5 = 2x margin)
            
        Returns:
            Required capital
        """
        notional = shares * current_price
        required = notional * margin_multiplier
        return required


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_exposure_pct(
    position_notional: Decimal,
    account_value: Decimal
) -> Decimal:
    """
    Calculate position exposure as % of account.
    
    Args:
        position_notional: Position value ($)
        account_value: Account equity ($)
        
    Returns:
        Exposure percentage (0.0 to 1.0)
    """
    if account_value <= 0:
        return Decimal("1.0")  # Treat as 100% exposed (conservative)
    
    return position_notional / account_value


def get_total_exposure(
    positions: list[dict],
    account_value: Decimal
) -> Decimal:
    """
    Calculate total notional exposure across all positions.
    
    Args:
        positions: List of positions with 'notional' field
        account_value: Current account equity
        
    Returns:
        Total exposure percentage (0.0 to 1.0+)
    """
    total_notional = sum(
        Decimal(str(pos.get('notional', 0)))
        for pos in positions
    )
    
    return calculate_exposure_pct(total_notional, account_value)
