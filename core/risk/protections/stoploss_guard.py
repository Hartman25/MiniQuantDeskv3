"""
Stoploss Guard Protection

Blocks trading a symbol after consecutive stoplosses.

Example:
    After 3 consecutive losses on SPY, block SPY trading for 1 hour.
    
Pattern: Freqtrade StoplossGuard
"""

from datetime import timedelta
from typing import Optional, List
from decimal import Decimal

from .base import SymbolProtection, ProtectionResult, ProtectionTrigger


class StoplossGuard(SymbolProtection):
    """
    Block symbol after N consecutive stoplosses.
    
    Args:
        max_stoplosses: Number of consecutive losses before blocking
        lookback_period: Time window to check (e.g., '1h', '4h', '1d')
        cooldown_duration: How long to block (e.g., '30m', '1h', '2h')
    
    Usage:
        guard = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(hours=1),
            cooldown_duration=timedelta(hours=1)
        )
        
        result = guard.check(
            symbol='SPY',
            completed_trades=recent_trades
        )
        
        if result.is_protected:
            # Don't trade SPY
            pass
    """
    
    def __init__(
        self,
        max_stoplosses: int = 3,
        lookback_period: timedelta = timedelta(hours=1),
        cooldown_duration: timedelta = timedelta(hours=1),
        enabled: bool = True
    ):
        super().__init__(name="StoplossGuard", enabled=enabled)
        
        self.max_stoplosses = max_stoplosses
        self.lookback_period = lookback_period
        self.cooldown_duration = cooldown_duration
    
    def _check_impl(
        self,
        symbol: str,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Check for consecutive stoplosses"""
        if not completed_trades:
            return ProtectionResult(is_protected=False)
        
        # Filter trades for this symbol
        symbol_trades = [
            t for t in completed_trades
            if getattr(t, 'symbol', None) == symbol
        ]
        
        if not symbol_trades:
            return ProtectionResult(is_protected=False)
        
        # Sort by close time (most recent first)
        symbol_trades.sort(
            key=lambda t: getattr(t, 'close_timestamp', t.get('close_timestamp')),
            reverse=True
        )
        
        # Count consecutive losses
        consecutive_losses = 0
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        cutoff_time = now - self.lookback_period
        
        for trade in symbol_trades:
            # Get close time
            close_time = getattr(trade, 'close_timestamp', None)
            if not close_time:
                close_time = trade.get('close_timestamp')
            
            # Outside lookback window
            if close_time < cutoff_time:
                break
            
            # Get profit
            profit = getattr(trade, 'profit', None)
            if profit is None:
                profit = trade.get('profit', 0)
            
            # Check if loss
            if profit < 0:
                consecutive_losses += 1
            else:
                # Winning trade breaks streak
                break
        
        # Trigger protection if exceeded
        if consecutive_losses >= self.max_stoplosses:
            return self._trigger_symbol_protection(
                symbol=symbol,
                duration=self.cooldown_duration,
                trigger=ProtectionTrigger.STOPLOSS_STREAK,
                reason=f"{consecutive_losses} consecutive losses on {symbol}",
                metadata={
                    'consecutive_losses': consecutive_losses,
                    'max_allowed': self.max_stoplosses,
                    'lookback_hours': self.lookback_period.total_seconds() / 3600
                }
            )
        
        return ProtectionResult(is_protected=False)
