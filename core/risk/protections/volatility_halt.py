"""
VolatilityHaltProtection

Blocks trading when realized volatility over a short window exceeds a threshold.
This reduces the chance a micro account gets wiped on spike/whipsaw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import math

from .base import ProtectionContext, ProtectionDecision, ProtectionResult, ProtectionTrigger


@dataclass
class VolatilityHaltProtection:
    """
    Volatility-based trading halt protection.
    
    Monitors price volatility over a rolling window and blocks trading
    when volatility exceeds threshold.
    """
    
    name: str = "VolatilityHalt"
    lookback_bars: int = 30
    threshold_std_devs: Decimal = Decimal("3.0")
    halt_duration_minutes: int = 30
    min_points: int = 20
    max_std: Decimal = field(default=Decimal("0.006"))  # Backwards compat
    
    def __post_init__(self):
        """Initialize price tracking"""
        # Per-symbol rolling price buffer
        self._price_buffer: Dict[str, List[Decimal]] = {}
        
        # Halted symbols and when halt expires
        self._halted_until: Dict[str, datetime] = {}
    
    def update_market_data(self, symbol: str, price: Decimal) -> None:
        """
        Update market data for volatility calculation.
        
        Args:
            symbol: Symbol to update
            price: Current price
        """
        if symbol not in self._price_buffer:
            self._price_buffer[symbol] = []
        
        # Add price to buffer
        self._price_buffer[symbol].append(price)
        
        # Keep only last lookback_bars prices
        if len(self._price_buffer[symbol]) > self.lookback_bars:
            self._price_buffer[symbol] = self._price_buffer[symbol][-self.lookback_bars:]
    
    def check(self, ctx: ProtectionContext = None, symbol: Optional[str] = None) -> ProtectionDecision:
        """
        Check if volatility halt should be triggered.
        
        Args:
            ctx: Protection context (can contain symbol)
            symbol: Symbol to check (if not in ctx)
            
        Returns:
            ProtectionDecision with .allowed attribute
        """
        # Get symbol from context or parameter
        check_symbol = None
        if ctx and ctx.symbol:
            check_symbol = ctx.symbol
        elif symbol:
            check_symbol = symbol
        
        if not check_symbol:
            # No symbol to check
            return ProtectionDecision(allow=True)
        
        # Check if currently halted
        if check_symbol in self._halted_until:
            until = self._halted_until[check_symbol]
            now = datetime.now(timezone.utc)
            
            if now < until:
                # Still halted
                return ProtectionDecision(
                    allow=False,
                    reason=f"Volatility halt active for {check_symbol}",
                    until=until
                )
            else:
                # Halt expired
                del self._halted_until[check_symbol]
        
        # Check if we have enough data
        if check_symbol not in self._price_buffer:
            return ProtectionDecision(allow=True, reason="insufficient_data")
        
        prices = self._price_buffer[check_symbol]
        
        if len(prices) < self.min_points:
            return ProtectionDecision(allow=True, reason="insufficient_data")
        
        # Calculate returns
        returns = []
        for i in range(1, len(prices)):
            ret = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(ret)
        
        if len(returns) < self.min_points:
            return ProtectionDecision(allow=True, reason="insufficient_returns")
        
        # Calculate standard deviation
        mean_return = sum(returns) / Decimal(len(returns))
        variance = sum((r - mean_return) ** 2 for r in returns) / Decimal(len(returns))
        
        # Use math.sqrt to avoid Decimal ** Decimal issues
        std_dev = Decimal(str(math.sqrt(float(variance))))
        
        # Check against threshold
        if std_dev > self.max_std:
            # Trigger halt
            now = datetime.now(timezone.utc)
            until = now + timedelta(minutes=self.halt_duration_minutes)
            self._halted_until[check_symbol] = until
            
            return ProtectionDecision(
                allow=False,
                reason=f"Volatility halt: std_dev={std_dev:.6f} > threshold={self.max_std}",
                until=until
            )
        
        return ProtectionDecision(allow=True)
    
    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        """Hook for trade submission (no-op)"""
        return None
    
    def reset_day(self, day) -> None:
        """Reset daily state"""
        self._price_buffer.clear()
        self._halted_until.clear()
    
    def reset(self):
        """Reset all state"""
        self._price_buffer.clear()
        self._halted_until.clear()


# Alias for backward compatibility and test imports
VolatilityHalt = VolatilityHaltProtection


__all__ = ['VolatilityHaltProtection', 'VolatilityHalt']