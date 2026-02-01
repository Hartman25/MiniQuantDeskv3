"""
Time abstraction layer for MiniQuantDesk.

Provides injectable clock that can be:
- Real-time (for live/paper trading)
- Simulated (for backtesting)

Eliminates hidden lookahead bugs and makes time testable.

Pattern stolen from: LEAN (QuantConnect)
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional
import pytz


class Clock(ABC):
    """Abstract clock interface"""
    
    @abstractmethod
    def now(self) -> datetime:
        """Get current time (always UTC)"""
        pass
    
    @abstractmethod
    def now_local(self, tz: str = "America/New_York") -> datetime:
        """Get current time in specified timezone"""
        pass
    
    @abstractmethod
    def is_market_hours(self) -> bool:
        """Check if currently in market hours (9:30-16:00 ET)"""
        pass


class RealTimeClock(Clock):
    """Real-time clock for live/paper trading"""
    
    def __init__(self, user_tz: Optional[str] = None, tz: Optional[str] = None):
        self._market_tz = pytz.timezone("America/New_York")
        # Accept user_tz and tz for compatibility but use UTC for market operations
        
    def now(self) -> datetime:
        """Current UTC time"""
        return datetime.now(timezone.utc)
    
    def now_local(self, tz: str = "America/New_York") -> datetime:
        """Current time in specified timezone"""
        timezone_obj = pytz.timezone(tz)
        return self.now().astimezone(timezone_obj)
    
    def is_market_hours(self) -> bool:
        """Check if currently in market hours"""
        et_now = self.now_local("America/New_York")
        
        # Weekend check
        if et_now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
        
        # Time check (9:30 AM - 4:00 PM ET)
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return market_open <= et_now < market_close


class BacktestClock(Clock):
    """Simulated clock for backtesting"""
    
    def __init__(self, start_time: datetime):
        """
        Args:
            start_time: Initial simulation time (must be UTC)
        """
        if start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (UTC)")
        
        self._current_time = start_time.astimezone(timezone.utc)
        self._market_tz = pytz.timezone("America/New_York")
        
    def now(self) -> datetime:
        """Current simulated time"""
        return self._current_time
    
    def now_local(self, tz: str = "America/New_York") -> datetime:
        """Current simulated time in specified timezone"""
        timezone_obj = pytz.timezone(tz)
        return self._current_time.astimezone(timezone_obj)
    
    def is_market_hours(self) -> bool:
        """Check if simulated time is in market hours"""
        et_now = self.now_local("America/New_York")
        
        # Weekend check
        if et_now.weekday() >= 5:
            return False
        
        # Time check
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return market_open <= et_now < market_close
    
    def advance(self, delta: timedelta):
        """
        Advance simulated time by delta.
        
        Args:
            delta: Time to advance
        """
        self._current_time += delta
        
    def set_time(self, new_time: datetime):
        """
        Set simulated time to specific value.
        
        Args:
            new_time: New time (must be UTC)
        """
        if new_time.tzinfo is None:
            raise ValueError("new_time must be timezone-aware (UTC)")
        
        self._current_time = new_time.astimezone(timezone.utc)


class ClockFactory:
    """Factory for creating appropriate clock"""
    
    @staticmethod
    def create_for_mode(mode: str, **kwargs) -> Clock:
        """
        Create clock based on mode.
        
        Args:
            mode: 'live', 'paper', or 'backtest'
            **kwargs: For backtest mode, pass start_time
            
        Returns:
            Appropriate clock implementation
        """
        if mode in ('live', 'paper'):
            return RealTimeClock()
        elif mode == 'backtest':
            start_time = kwargs.get('start_time')
            if not start_time:
                raise ValueError("backtest mode requires start_time parameter")
            return BacktestClock(start_time)
        else:
            raise ValueError(f"Unknown mode: {mode}")


# Backwards compatibility alias
SystemClock = RealTimeClock


# Convenience function for getting clock from config
def get_clock(config: dict) -> Clock:
    """
    Get clock from config.
    
    Args:
        config: Configuration dict with 'account.mode' key
        
    Returns:
        Appropriate clock implementation
    """
    mode = config.get('account', {}).get('mode', 'paper')
    
    if mode == 'backtest':
        # For backtest, require start_time in config
        start_time = config.get('backtest', {}).get('start_time')
        if not start_time:
            raise ValueError("Backtest mode requires backtest.start_time in config")
        return BacktestClock(start_time)
    else:
        return RealTimeClock()
