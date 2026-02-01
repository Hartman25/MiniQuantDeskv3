"""
TimeWindowProtection

Blocks trading outside configured time window (market timezone).

This is a GLOBAL protection - blocks all trading outside allowed hours.
Critical for strategies with specific market regime requirements.
"""

from datetime import datetime, time, timezone, timedelta
from typing import Optional, List
from zoneinfo import ZoneInfo

from .base import GlobalProtection, ProtectionResult, ProtectionContext, ProtectionDecision, ProtectionTrigger


class TimeWindowProtection(GlobalProtection):
    """
    Global protection: block trading outside time window.
    
    Example: VWAPMicroMeanReversion only trades 10:00-11:30 ET
    to avoid open/close volatility and ensure adequate liquidity.
    """
    
    def __init__(
        self,
        name: Optional[str] = None,
        start_time: time = time(10, 0),
        end_time: time = time(11, 30),
        timezone_str: str = "America/New_York",
        enabled: bool = True,
        clock=None  # Accept clock kwarg for test compatibility
    ):
        """
        Args:
            name: Optional custom name (defaults to "TimeWindow")
            start_time: Trading window start (default 10:00)
            end_time: Trading window end (default 11:30)
            timezone_str: Timezone for window (default ET)
            enabled: Whether protection is active
            clock: Optional clock instance (for testing)
        """
        protection_name = name if name is not None else "TimeWindow"
        super().__init__(name=protection_name, enabled=enabled)
        self.start_time = start_time
        self.end_time = end_time
        self.timezone = ZoneInfo(timezone_str)
        self.clock = clock  # Store for potential use
    
    def _check_impl(
        self,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Check if current time is within trading window"""
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(self.timezone)
        current_time = now_local.time()
        
        # Check if outside window
        if current_time < self.start_time or current_time > self.end_time:
            return ProtectionResult(
                is_protected=True,
                reason=f"Outside trading window: {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} {self.timezone}",
                trigger=ProtectionTrigger.TIME_WINDOW,
                metadata={
                    'current_time': current_time.isoformat(),
                    'window_start': self.start_time.isoformat(),
                    'window_end': self.end_time.isoformat(),
                    'timezone': str(self.timezone)
                }
            )
        
        return ProtectionResult(is_protected=False)
    
    def check_with_context(self, ctx: ProtectionContext) -> ProtectionDecision:
        """
        Check using ProtectionContext (Phase 3 compatible).
        
        Args:
            ctx: Protection context
            
        Returns:
            ProtectionDecision with .allowed attribute
        """
        result = self._check_impl(None, None)
        
        if result.is_protected:
            # Return decision with allowed=False (blocked)
            return ProtectionDecision(
                allow=False,
                reason=result.reason,
                until=result.until
            )
        else:
            # Return decision with allowed=True
            return ProtectionDecision(allow=True)
    
    def reset(self):
        """No state to reset for time window"""
        pass
    
    def get_status(self) -> dict:
        """Get current time window status"""
        base_status = super().get_status()
        
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(self.timezone)
        current_time = now_local.time()
        
        in_window = self.start_time <= current_time <= self.end_time
        
        base_status.update({
            'window_start': self.start_time.isoformat(),
            'window_end': self.end_time.isoformat(),
            'timezone': str(self.timezone),
            'current_time': current_time.isoformat(),
            'in_window': in_window
        })
        
        return base_status
