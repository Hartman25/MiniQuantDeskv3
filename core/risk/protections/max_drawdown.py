"""
Max Drawdown Protection

Blocks ALL trading when drawdown exceeds threshold.

Example:
    If account down 15% from peak, stop all trading for 24 hours.
    
Pattern: Freqtrade MaxDrawdownProtection
"""

from datetime import timedelta, datetime, timezone
from typing import Optional, List
from decimal import Decimal

from .base import GlobalProtection, ProtectionResult, ProtectionTrigger


class MaxDrawdownProtection(GlobalProtection):
    """
    Block all trading after max drawdown exceeded.
    
    Args:
        max_drawdown: Maximum allowed drawdown (0.15 = 15%)
        cooldown_duration: How long to block trading
        lookback_period: Time window to calculate drawdown
    
    Usage:
        protection = MaxDrawdownProtection(
            max_drawdown=0.15,  # 15%
            cooldown_duration=timedelta(hours=24),
            lookback_period=timedelta(days=7)
        )
        
        result = protection.check(
            completed_trades=recent_trades
        )
        
        if result.is_protected:
            # Stop ALL trading
            pass
    """
    
    def __init__(
        self,
        max_drawdown: float = 0.15,
        cooldown_duration: timedelta = timedelta(hours=24),
        lookback_period: timedelta = timedelta(days=7),
        enabled: bool = True
    ):
        super().__init__(name="MaxDrawdownProtection", enabled=enabled)
        
        self.max_drawdown = max_drawdown
        self.cooldown_duration = cooldown_duration
        self.lookback_period = lookback_period
        
        self._peak_balance = Decimal('0')
        self._current_balance = Decimal('0')
    
    def _check_impl(
        self,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Check if drawdown exceeded"""
        if not completed_trades:
            return ProtectionResult(is_protected=False)
        
        # Calculate balance from trades
        now = datetime.now(timezone.utc)
        cutoff_time = now - self.lookback_period
        
        # Get trades in lookback window
        recent_trades = [
            t for t in completed_trades
            if getattr(t, 'close_timestamp', t.get('close_timestamp')) >= cutoff_time
        ]
        
        if not recent_trades:
            return ProtectionResult(is_protected=False)
        
        # Calculate cumulative P&L
        cumulative_pnl = []
        running_total = Decimal('0')
        
        for trade in sorted(recent_trades, key=lambda t: getattr(t, 'close_timestamp', t.get('close_timestamp'))):
            profit = Decimal(str(getattr(trade, 'profit', trade.get('profit', 0))))
            running_total += profit
            cumulative_pnl.append(running_total)
        
        # Find peak and current
        if cumulative_pnl:
            peak = max(cumulative_pnl)
            current = cumulative_pnl[-1]
            
            # Calculate drawdown
            if peak > 0:
                drawdown = float((peak - current) / peak)
            else:
                drawdown = 0.0
            
            self._peak_balance = peak
            self._current_balance = current
            
            # Check if exceeded
            if drawdown > self.max_drawdown:
                return self._trigger_protection(
                    duration=self.cooldown_duration,
                    trigger=ProtectionTrigger.MAX_DRAWDOWN,
                    reason=f"Drawdown {drawdown:.1%} exceeds limit {self.max_drawdown:.1%}",
                    metadata={
                        'drawdown': f"{drawdown:.2%}",
                        'max_allowed': f"{self.max_drawdown:.2%}",
                        'peak_balance': str(peak),
                        'current_balance': str(current),
                        'lookback_days': self.lookback_period.days
                    }
                )
        
        return ProtectionResult(is_protected=False)
    
    def reset(self):
        """Reset protection"""
        super().reset()
        self._peak_balance = Decimal('0')
        self._current_balance = Decimal('0')
