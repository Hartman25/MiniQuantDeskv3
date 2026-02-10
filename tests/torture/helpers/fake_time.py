"""
Deterministic time control for torture tests.

Patches time.sleep to record requested durations without actually sleeping.
Provides a controllable "now" clock for adaptive-sleep tests.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from unittest.mock import patch


class FakeClock:
    """Deterministic clock that can be advanced manually.

    Usage:
        clock = FakeClock(start=datetime(2026, 2, 9, 3, 0, tzinfo=timezone.utc))
        clock.advance(seconds=120)
        print(clock.now())  # 2026-02-09 03:02:00 UTC
    """

    def __init__(self, start: Optional[datetime] = None) -> None:
        self._now = start or datetime(2026, 2, 9, 3, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float = 0, minutes: float = 0, hours: float = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours)

    def set(self, dt: datetime) -> None:
        self._now = dt


class SleepRecorder:
    """Drop-in replacement for time.sleep that records durations.

    Never actually sleeps.  Optionally advances a FakeClock when "sleeping".
    """

    def __init__(self, clock: Optional[FakeClock] = None) -> None:
        self.calls: List[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(seconds=seconds)

    @property
    def total_slept(self) -> float:
        return sum(self.calls)

    @property
    def call_count(self) -> int:
        return len(self.calls)
