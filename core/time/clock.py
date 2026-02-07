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


# ============================================================================
# PATCH 12: Time normalization helpers + MarketSession
# ============================================================================

from dataclasses import dataclass
from typing import Tuple
import time as _time_mod


def utc_now() -> datetime:
    """
    Canonical way to get the current UTC time as a timezone-aware datetime.

    Use this instead of ``datetime.now()`` or ``datetime.now(timezone.utc)``
    everywhere so that grep can verify no raw ``datetime.now()`` calls remain.
    """
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """
    Ensure *dt* is timezone-aware and in UTC.

    - If naive (no tzinfo): attach UTC (assumes caller meant UTC).
    - If aware but not UTC: convert to UTC.
    - If already UTC-aware: return as-is.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch_ms(dt: Optional[datetime] = None) -> int:
    """
    Return milliseconds since Unix epoch for *dt* (or now if None).

    Always produces a deterministic result regardless of host timezone.
    """
    if dt is None:
        return int(_time_mod.time() * 1000)
    return int(ensure_utc(dt).timestamp() * 1000)


@dataclass(frozen=True)
class SessionBoundary:
    """
    One named time window within a trading day.

    All times are in **market timezone** (America/New_York for US equities).
    """
    name: str            # "pre_market", "regular", "after_hours"
    open_hour: int
    open_minute: int
    close_hour: int
    close_minute: int

    def contains(self, hour: int, minute: int) -> bool:
        """Return True if (hour, minute) falls within [open, close)."""
        open_val = self.open_hour * 60 + self.open_minute
        close_val = self.close_hour * 60 + self.close_minute
        current = hour * 60 + minute
        return open_val <= current < close_val


class MarketSession:
    """
    Defines session boundaries for a market.

    Provides structured queries:
      - is_regular_hours(dt)
      - is_pre_market(dt)
      - is_after_hours(dt)
      - is_any_session(dt)
      - current_session(dt) → name or None
      - next_open(dt) → datetime of next regular open

    All methods accept UTC-aware datetimes and internally convert to
    market timezone.  Weekends always return False.
    """

    # US Equity defaults
    PRE_MARKET = SessionBoundary("pre_market", 4, 0, 9, 30)
    REGULAR = SessionBoundary("regular", 9, 30, 16, 0)
    AFTER_HOURS = SessionBoundary("after_hours", 16, 0, 20, 0)

    def __init__(
        self,
        market_tz: str = "America/New_York",
        pre_market: Optional[SessionBoundary] = None,
        regular: Optional[SessionBoundary] = None,
        after_hours: Optional[SessionBoundary] = None,
    ) -> None:
        self._market_tz_name = market_tz
        try:
            from zoneinfo import ZoneInfo
            self._market_tz = ZoneInfo(market_tz)
        except Exception:
            self._market_tz = pytz.timezone(market_tz)

        self._pre_market = pre_market or self.PRE_MARKET
        self._regular = regular or self.REGULAR
        self._after_hours = after_hours or self.AFTER_HOURS
        self._sessions = [self._pre_market, self._regular, self._after_hours]

    def _to_market(self, dt: datetime) -> datetime:
        return ensure_utc(dt).astimezone(self._market_tz)

    def _is_weekday(self, dt: datetime) -> bool:
        return self._to_market(dt).weekday() < 5

    def is_regular_hours(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or utc_now()
        if not self._is_weekday(dt):
            return False
        mt = self._to_market(dt)
        return self._regular.contains(mt.hour, mt.minute)

    def is_pre_market(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or utc_now()
        if not self._is_weekday(dt):
            return False
        mt = self._to_market(dt)
        return self._pre_market.contains(mt.hour, mt.minute)

    def is_after_hours(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or utc_now()
        if not self._is_weekday(dt):
            return False
        mt = self._to_market(dt)
        return self._after_hours.contains(mt.hour, mt.minute)

    def is_any_session(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or utc_now()
        if not self._is_weekday(dt):
            return False
        mt = self._to_market(dt)
        return any(s.contains(mt.hour, mt.minute) for s in self._sessions)

    def current_session(self, dt: Optional[datetime] = None) -> Optional[str]:
        """Return the name of the current session, or None if outside all."""
        dt = dt or utc_now()
        if not self._is_weekday(dt):
            return None
        mt = self._to_market(dt)
        for s in self._sessions:
            if s.contains(mt.hour, mt.minute):
                return s.name
        return None

    def regular_open_close(self, dt: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        """
        Return (open, close) as UTC datetimes for the regular session
        on the same calendar day as *dt* (in market tz).
        """
        dt = dt or utc_now()
        mt = self._to_market(dt)
        day = mt.date()

        # Build market-tz aware datetimes then convert to UTC
        open_mt = mt.replace(
            hour=self._regular.open_hour,
            minute=self._regular.open_minute,
            second=0, microsecond=0,
        )
        close_mt = mt.replace(
            hour=self._regular.close_hour,
            minute=self._regular.close_minute,
            second=0, microsecond=0,
        )
        return ensure_utc(open_mt), ensure_utc(close_mt)


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
