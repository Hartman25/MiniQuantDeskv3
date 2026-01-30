"""
Cooldown Period Protection

Forces a waiting period after major loss.

Example:
    After losing $500 in one trade, wait 30 minutes before trading again.
    
Pattern: Freqtrade CooldownPeriod (custom addition)
"""

from datetime import timedelta, datetime, timezone
from typing import Optional, List
from decimal import Decimal

from .base import GlobalProtection, ProtectionResult, ProtectionTrigger


class CooldownPeriod(GlobalProtection):
    """
    Force cooldown after major loss.
    
    Args:
        loss_threshold: Loss amount that triggers cooldown
        cooldown_duration: How long to wait
    
    Usage:
        cooldown = CooldownPeriod(
            loss_threshold=Decimal('500'),
            cooldown_duration=timedelta(minutes=30)
        )
        
        result = cooldown.check(
            completed_trades=recent_trades
        )
    """
    
    def __init__(
        self,
        loss_threshold: Decimal = Decimal('500'),
        cooldown_duration: timedelta = timedelta(minutes=30),
        enabled: bool = True
    ):
        super().__init__(name="CooldownPeriod", enabled=enabled)
        
        self.loss_threshold = loss_threshold
        self.cooldown_duration = cooldown_duration
        self._last_check_time = datetime.now(timezone.utc)
    
    def _check_impl(
        self,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Check for major loss"""
        if not completed_trades:
            return ProtectionResult(is_protected=False)
        
        now = datetime.now(timezone.utc)
        
        # Only check trades since last check
        new_trades = [
            t for t in completed_trades
            if getattr(t, 'close_timestamp', t.get('close_timestamp')) > self._last_check_time
        ]
        
        self._last_check_time = now
        
        # Check for major loss in new trades
        for trade in new_trades:
            profit = Decimal(str(getattr(trade, 'profit', trade.get('profit', 0))))
            
            if abs(profit) >= self.loss_threshold and profit < 0:
                # Major loss detected
                symbol = getattr(trade, 'symbol', trade.get('symbol', 'UNKNOWN'))
                
                return self._trigger_protection(
                    duration=self.cooldown_duration,
                    trigger=ProtectionTrigger.COOLDOWN_PERIOD,
                    reason=f"Major loss ${abs(profit)} on {symbol}, cooling down",
                    metadata={
                        'loss_amount': str(abs(profit)),
                        'threshold': str(self.loss_threshold),
                        'symbol': symbol,
                        'cooldown_minutes': self.cooldown_duration.total_seconds() / 60
                    }
                )
        
        return ProtectionResult(is_protected=False)
    
    def reset(self):
        """Reset cooldown"""
        super().reset()
        self._last_check_time = datetime.now(timezone.utc)
