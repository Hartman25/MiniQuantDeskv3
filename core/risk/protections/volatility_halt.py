"""
VolatilityHaltProtection

Blocks trading when realized volatility over a short window exceeds a threshold.
This reduces the chance a micro account gets wiped on spike/whipsaw.

Caller can provide recent returns in ctx.extra["recent_returns"], or use update_market_data()
to build a rolling price buffer per symbol.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
import math
from typing import Dict, List
from .base import ProtectionContext, ProtectionDecision, ProtectionResult, ProtectionTrigger


@dataclass
class VolatilityHaltProtection:
    name: str = "VolatilityHalt"
    max_std: Decimal = Decimal("0.006")  # 0.6% std over window
    min_points: int = 20
    # Backwards compatibility parameters (aliases)
    lookback_bars: int = None  # Alias for min_points
    threshold_std_devs: Decimal = None  # Alias for max_std
    halt_duration_minutes: int = None  # Not used but accepted for compatibility
    
    _price_buffer: Dict[str, List[Decimal]] = field(default_factory=dict, init=False, repr=False)
    
    def __post_init__(self):
        # Map compatibility aliases to actual fields
        if self.lookback_bars is not None:
            object.__setattr__(self, 'min_points', self.lookback_bars)
        if self.threshold_std_devs is not None:
            object.__setattr__(self, 'max_std', self.threshold_std_devs)
    
    def update_market_data(self, symbol: str, price: float) -> None:
        """
        Store rolling price data for volatility calculations.
        Maintains buffer of min_points most recent prices.
        """
        price_dec = Decimal(str(price))
        
        if symbol not in self._price_buffer:
            self._price_buffer[symbol] = []
        
        self._price_buffer[symbol].append(price_dec)
        
        # Keep only most recent min_points prices
        if len(self._price_buffer[symbol]) > self.min_points:
            self._price_buffer[symbol] = self._price_buffer[symbol][-self.min_points:]

    def check(self, symbol: str = None, **kwargs) -> ProtectionResult:
        """
        Check if volatility protection should trigger.
        
        Args:
            symbol: Symbol to check (uses buffered prices if available)
            **kwargs: Additional context (may include ctx for backwards compatibility)
            
        Returns:
            ProtectionResult with is_protected=True if volatility too high
        """
        # Use buffered prices if available
        if symbol and symbol in self._price_buffer and len(self._price_buffer[symbol]) >= self.min_points:
            prices = self._price_buffer[symbol]
            
            # Calculate returns
            returns = []
            for i in range(1, len(prices)):
                if prices[i-1] != 0:
                    ret = (prices[i] - prices[i-1]) / prices[i-1]
                    returns.append(float(ret))
            
            if len(returns) >= 2:
                # Calculate standard deviation safely
                mean = sum(returns) / len(returns)
                variance = sum((r - mean) ** 2 for r in returns) / len(returns)
                std_dev = Decimal(str(math.sqrt(variance)))
                
                if std_dev > self.max_std:
                    return ProtectionResult(
                        is_protected=True,
                        trigger=ProtectionTrigger.VOLATILITY_HALT,
                        reason=f"Volatility too high: std_dev={std_dev:.6f} > threshold={self.max_std}",
                        until=datetime.now() + timedelta(minutes=30)
                    )
        
        # If no buffered data or insufficient points, not protected
        return ProtectionResult(
            is_protected=False,
            trigger=None,
            reason="",
            until=None
        )

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        return None

    def reset_day(self, day) -> None:
        return None


# Backwards compatibility alias
VolatilityHalt = VolatilityHaltProtection
