"""
VolatilityProtection

Blocks trading when realized volatility exceeds threshold.
Protects micro accounts from spike/whipsaw events.

Usage:
    protection = VolatilityProtection(max_std=0.006)  # 0.6%
    
    # Update with recent prices
    protection.update_prices(symbol='SPY', prices=[450.1, 450.5, 449.8, ...])
    
    # Check if protected
    result = protection.check()
"""

from decimal import Decimal
from typing import Optional, List, Dict, Deque
from collections import deque

from .base import GlobalProtection, ProtectionResult


class VolatilityProtection(GlobalProtection):
    """
    Global protection: block trading during volatility spikes.
    
    Calculates rolling standard deviation of returns.
    Blocks all trading when volatility exceeds threshold.
    """
    
    def __init__(
        self,
        max_std: Decimal = Decimal("0.006"),  # 0.6% volatility threshold
        min_points: int = 20,  # Minimum data points needed
        lookback: int = 60,  # Rolling window size
        enabled: bool = True
    ):
        """
        Args:
            max_std: Maximum allowed volatility (std dev of returns)
            min_points: Minimum prices needed before checking
            lookback: Number of prices to keep in rolling window
            enabled: Whether protection is active
        """
        super().__init__(name="VolatilityHalt", enabled=enabled)
        self.max_std = max_std
        self.min_points = min_points
        self.lookback = lookback
        
        # Store recent prices per symbol for volatility calculation
        self._price_history: Dict[str, Deque[Decimal]] = {}
        self._last_volatility: Optional[Decimal] = None
    
    def update_prices(self, symbol: str, prices: List[Decimal]) -> None:
        """
        Update price history for a symbol.
        
        Args:
            symbol: Symbol to update
            prices: List of recent prices (will keep last 'lookback' prices)
        """
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self.lookback)
        
        # Add prices to rolling window
        for price in prices:
            self._price_history[symbol].append(Decimal(str(price)))
    
    def add_price(self, symbol: str, price: Decimal) -> None:
        """
        Add single price to history.
        
        Args:
            symbol: Symbol to update
            price: Latest price
        """
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self.lookback)
        
        self._price_history[symbol].append(Decimal(str(price)))
    
    def _calculate_volatility(self, prices: List[Decimal]) -> Optional[Decimal]:
        """
        Calculate standard deviation of returns.
        
        Args:
            prices: List of prices
            
        Returns:
            Standard deviation or None if insufficient data
        """
        if len(prices) < 2:
            return None
        
        # Calculate returns: (price[i] - price[i-1]) / price[i-1]
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = (prices[i] - prices[i-1]) / prices[i-1]
                returns.append(ret)
        
        if len(returns) < self.min_points:
            return None
        
        # Use last min_points returns
        recent_returns = returns[-self.min_points:]
        
        # Calculate mean
        mean = sum(recent_returns) / Decimal(len(recent_returns))
        
        # Calculate variance
        variance = sum((r - mean) ** 2 for r in recent_returns) / Decimal(len(recent_returns))
        
        # Calculate standard deviation (crude sqrt via float)
        std = Decimal(str(float(variance) ** 0.5))
        
        return std
    
    def _check_impl(
        self,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Check if volatility exceeds threshold"""
        
        # If no price history, allow trading (insufficient data)
        if not self._price_history:
            return ProtectionResult(is_protected=False)
        
        # Calculate volatility for each symbol
        max_volatility = Decimal("0")
        high_vol_symbols = []
        
        for symbol, prices in self._price_history.items():
            if len(prices) < 2:
                continue
            
            vol = self._calculate_volatility(list(prices))
            if vol is None:
                continue
            
            if vol > max_volatility:
                max_volatility = vol
            
            if vol >= self.max_std:
                high_vol_symbols.append(f"{symbol}:{vol:.4f}")
        
        # Store last volatility for status
        self._last_volatility = max_volatility
        
        # Block if any symbol exceeds threshold
        if high_vol_symbols:
            return ProtectionResult(
                is_protected=True,
                reason=f"Volatility spike detected: {', '.join(high_vol_symbols)} (max={self.max_std})",
                metadata={
                    'max_volatility': float(max_volatility),
                    'threshold': float(self.max_std),
                    'high_vol_symbols': high_vol_symbols,
                    'symbols_tracked': len(self._price_history)
                }
            )
        
        return ProtectionResult(is_protected=False)
    
    def reset(self):
        """Clear all price history"""
        self._price_history.clear()
        self._last_volatility = None
    
    def get_status(self) -> dict:
        """Get current volatility protection status"""
        base_status = super().get_status()
        
        base_status.update({
            'max_std_threshold': float(self.max_std),
            'min_points_required': self.min_points,
            'symbols_tracked': len(self._price_history),
            'last_volatility': float(self._last_volatility) if self._last_volatility else None,
            'data_points': {
                symbol: len(prices) 
                for symbol, prices in self._price_history.items()
            }
        })
        
        return base_status
