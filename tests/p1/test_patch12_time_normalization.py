"""
PATCH 12 — Normalize time and session boundaries

INVARIANT:
    All datetimes in the system are UTC-aware.  Session boundaries are
    defined via MarketSession and queried uniformly.  ``utc_now()`` is
    the canonical way to get current time.

TESTS:
    1.  utc_now() returns UTC-aware datetime.
    2.  ensure_utc() on naive datetime attaches UTC.
    3.  ensure_utc() converts non-UTC aware to UTC.
    4.  ensure_utc() on UTC-aware is identity.
    5.  epoch_ms() returns int milliseconds.
    6.  epoch_ms(None) ≈ current time.
    7.  SessionBoundary.contains() respects [open, close).
    8.  MarketSession.is_regular_hours() on weekday at 10am ET.
    9.  MarketSession.is_regular_hours() on weekend → False.
   10.  MarketSession.is_pre_market() at 5am ET.
   11.  MarketSession.is_after_hours() at 5pm ET.
   12.  MarketSession.current_session() returns correct name.
   13.  MarketSession.current_session() outside hours → None.
   14.  MarketSession.is_any_session() covers all windows.
   15.  MarketSession.regular_open_close() returns UTC pair.
   16.  ensure_utc() on naive midnight matches UTC midnight.
"""

import time as _time
from datetime import datetime, timezone, timedelta

import pytest

from core.time.clock import (
    MarketSession,
    SessionBoundary,
    ensure_utc,
    epoch_ms,
    utc_now,
)


class TestUtcNow:

    def test_returns_utc_aware(self):
        now = utc_now()
        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc

    def test_close_to_real_time(self):
        before = _time.time()
        now = utc_now()
        after = _time.time()
        # Allow tiny floating-point epsilon
        assert before - 0.001 <= now.timestamp() <= after + 0.001


class TestEnsureUtc:

    def test_naive_gets_utc(self):
        naive = datetime(2025, 6, 15, 12, 0, 0)
        result = ensure_utc(naive)
        assert result.tzinfo == timezone.utc
        assert result.hour == 12  # preserved as-is

    def test_non_utc_converted(self):
        import pytz
        eastern = pytz.timezone("America/New_York")
        et = eastern.localize(datetime(2025, 6, 15, 10, 0, 0))  # 10am ET = 14:00 UTC (summer)
        result = ensure_utc(et)
        assert result.tzinfo == timezone.utc
        assert result.hour == 14  # EDT offset = -4

    def test_utc_is_identity(self):
        orig = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = ensure_utc(orig)
        assert result == orig
        assert result.tzinfo == timezone.utc

    def test_naive_midnight(self):
        naive = datetime(2025, 1, 1, 0, 0, 0)
        result = ensure_utc(naive)
        assert result == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestEpochMs:

    def test_returns_int(self):
        dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ms = epoch_ms(dt)
        assert isinstance(ms, int)
        assert ms == 1735689600000

    def test_none_is_current(self):
        before = int(_time.time() * 1000)
        ms = epoch_ms(None)
        after = int(_time.time() * 1000)
        assert before <= ms <= after + 1


class TestSessionBoundary:

    def test_contains_in_range(self):
        s = SessionBoundary("regular", 9, 30, 16, 0)
        assert s.contains(10, 0) is True
        assert s.contains(9, 30) is True   # open is inclusive
        assert s.contains(15, 59) is True

    def test_contains_at_close_excluded(self):
        s = SessionBoundary("regular", 9, 30, 16, 0)
        assert s.contains(16, 0) is False  # close is exclusive

    def test_contains_before_open(self):
        s = SessionBoundary("regular", 9, 30, 16, 0)
        assert s.contains(9, 29) is False


class TestMarketSession:

    def _make_utc(self, year, month, day, hour, minute):
        """Build a UTC-aware datetime."""
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

    def test_regular_hours_weekday_10am_et(self):
        """10:00 AM ET on a Wednesday = 14:00 UTC (summer) → regular hours."""
        # 2025-06-18 is a Wednesday
        session = MarketSession()
        # 10am ET in summer (EDT=-4) = 14:00 UTC
        dt = self._make_utc(2025, 6, 18, 14, 0)
        assert session.is_regular_hours(dt) is True

    def test_regular_hours_weekend_false(self):
        """Saturday → never regular hours."""
        session = MarketSession()
        # 2025-06-21 is a Saturday
        dt = self._make_utc(2025, 6, 21, 14, 0)
        assert session.is_regular_hours(dt) is False

    def test_pre_market_5am_et(self):
        """5:00 AM ET on a weekday → pre-market."""
        session = MarketSession()
        # 5am ET in summer = 9:00 UTC
        dt = self._make_utc(2025, 6, 18, 9, 0)
        assert session.is_pre_market(dt) is True
        assert session.is_regular_hours(dt) is False

    def test_after_hours_5pm_et(self):
        """5:00 PM ET on a weekday → after hours."""
        session = MarketSession()
        # 5pm ET in summer = 21:00 UTC
        dt = self._make_utc(2025, 6, 18, 21, 0)
        assert session.is_after_hours(dt) is True
        assert session.is_regular_hours(dt) is False

    def test_current_session_regular(self):
        session = MarketSession()
        # 14:00 UTC = 10am ET (summer)
        dt = self._make_utc(2025, 6, 18, 14, 0)
        assert session.current_session(dt) == "regular"

    def test_current_session_outside(self):
        session = MarketSession()
        # 2am UTC = 10pm ET previous day → outside all
        dt = self._make_utc(2025, 6, 18, 2, 0)
        assert session.current_session(dt) is None

    def test_is_any_session_covers_all(self):
        session = MarketSession()
        # Pre-market: 5am ET = 9:00 UTC (summer)
        assert session.is_any_session(self._make_utc(2025, 6, 18, 9, 0)) is True
        # Regular: 10am ET = 14:00 UTC
        assert session.is_any_session(self._make_utc(2025, 6, 18, 14, 0)) is True
        # After hours: 5pm ET = 21:00 UTC
        assert session.is_any_session(self._make_utc(2025, 6, 18, 21, 0)) is True
        # Outside: 2am ET = 6:00 UTC
        assert session.is_any_session(self._make_utc(2025, 6, 18, 6, 0)) is False

    def test_regular_open_close_returns_utc(self):
        session = MarketSession()
        dt = self._make_utc(2025, 6, 18, 14, 0)
        open_dt, close_dt = session.regular_open_close(dt)
        assert open_dt.tzinfo == timezone.utc
        assert close_dt.tzinfo == timezone.utc
        assert open_dt < close_dt
