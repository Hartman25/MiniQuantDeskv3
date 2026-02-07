"""
PATCH 3 — Make throttling real on market-data path

INVARIANT:
    Every external market-data fetch in MarketDataPipeline must go through the
    Throttler.  The throttler must expose execute_sync() and the pipeline must
    call it with limit_id='alpaca_data'.

TESTS:
    1. Throttler.execute_sync() exists and correctly forwards calls.
    2. Pipeline calls throttler.execute_sync() on every fetch (cache miss).
    3. Repeated fetches (cache hits) do NOT increment throttler call count.
    4. Throttler stats reflect real call counts.
    5. execute_sync() blocks when rate limit is reached (functional test).
    6. Dead duplicate code removed from throttler.py.
"""

import time
import pytest
import pandas as pd
from datetime import datetime, timezone

from core.net.throttler import Throttler, RateLimit, ExponentialBackoff
from core.data.pipeline import MarketDataPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _CountingThrottler:
    """Throttler substitute that counts execute_sync() calls."""

    def __init__(self):
        self.calls = 0
        self.limit_ids = []

    def execute_sync(self, limit_id, func, *args, **kwargs):
        self.calls += 1
        self.limit_ids.append(limit_id)
        return func(*args, **kwargs)


class _FakeBars:
    """Mimics the Alpaca BarSet response object."""

    def __init__(self, df):
        self._df = df

    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        class _Obj:
            def __init__(self, df):
                self.df = df
        return _Obj(self._df)


def _make_pipeline(throttler, monkeypatch):
    """Build a pipeline with a fake alpaca client."""
    p = MarketDataPipeline(
        alpaca_api_key="x",
        alpaca_api_secret="y",
        max_staleness_seconds=9999,
        cache_ttl_seconds=0,   # disable cache for most tests
        throttler=throttler,
    )

    def _fake_get_stock_bars(request):
        idx = pd.DatetimeIndex([datetime.now(timezone.utc)])
        df = pd.DataFrame(
            [{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}],
            index=idx,
        )
        return _FakeBars(df)

    monkeypatch.setattr(p.alpaca_client, "get_stock_bars", _fake_get_stock_bars)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExecuteSyncExists:
    """Throttler must expose execute_sync() alongside the async execute()."""

    def test_execute_sync_is_callable(self):
        t = Throttler({"test": RateLimit(10, 1.0)})
        assert callable(getattr(t, "execute_sync", None)), (
            "Throttler must have execute_sync() for synchronous callers"
        )

    def test_execute_sync_forwards_result(self):
        t = Throttler({"test": RateLimit(10, 1.0)})
        result = t.execute_sync("test", lambda: 42)
        assert result == 42

    def test_execute_sync_passes_args(self):
        t = Throttler({"test": RateLimit(10, 1.0)})
        result = t.execute_sync("test", lambda a, b: a + b, 3, 7)
        assert result == 10

    def test_execute_sync_passes_kwargs(self):
        t = Throttler({"test": RateLimit(10, 1.0)})
        result = t.execute_sync("test", lambda x=0: x * 2, x=5)
        assert result == 10


class TestPipelineUsesThrottler:
    """Pipeline.get_latest_bars() must route through throttler.execute_sync()."""

    def test_single_fetch_increments_call_count(self, monkeypatch):
        throttler = _CountingThrottler()
        p = _make_pipeline(throttler, monkeypatch)

        df = p.get_latest_bars("SPY", lookback_bars=1, timeframe="1Min")
        assert not df.empty
        assert throttler.calls == 1
        assert throttler.limit_ids[-1] == "alpaca_data"

    def test_repeated_fetches_increment_call_count(self, monkeypatch):
        throttler = _CountingThrottler()
        p = _make_pipeline(throttler, monkeypatch)

        for _ in range(5):
            p.get_latest_bars("SPY", lookback_bars=1, timeframe="1Min")

        assert throttler.calls == 5, (
            "Each cache miss must go through throttler"
        )

    def test_cache_hit_does_not_call_throttler(self, monkeypatch):
        throttler = _CountingThrottler()
        p = MarketDataPipeline(
            alpaca_api_key="x",
            alpaca_api_secret="y",
            max_staleness_seconds=9999,
            cache_ttl_seconds=60,   # long cache TTL
            throttler=throttler,
        )

        def _fake_get_stock_bars(request):
            idx = pd.DatetimeIndex([datetime.now(timezone.utc)])
            df = pd.DataFrame(
                [{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}],
                index=idx,
            )
            return _FakeBars(df)

        monkeypatch.setattr(p.alpaca_client, "get_stock_bars", _fake_get_stock_bars)

        p.get_latest_bars("SPY", lookback_bars=1)
        assert throttler.calls == 1

        # Second call should hit cache
        p.get_latest_bars("SPY", lookback_bars=1)
        assert throttler.calls == 1, (
            "Cache hit must NOT call throttler again"
        )


class TestThrottlerStats:
    """Throttler.get_stats() must reflect execute_sync() calls."""

    def test_stats_increment_after_execute_sync(self):
        t = Throttler({"alpaca_data": RateLimit(200, 60.0)})
        assert t.get_stats("alpaca_data")["total_requests"] == 0

        t.execute_sync("alpaca_data", lambda: None)
        assert t.get_stats("alpaca_data")["total_requests"] == 1

        t.execute_sync("alpaca_data", lambda: None)
        assert t.get_stats("alpaca_data")["total_requests"] == 2

    def test_reset_stats_clears_execute_sync_counts(self):
        t = Throttler({"test": RateLimit(10, 1.0)})
        for _ in range(5):
            t.execute_sync("test", lambda: None)
        assert t.get_stats("test")["total_requests"] == 5

        t.reset_stats()
        assert t.get_stats("test")["total_requests"] == 0


class TestExecuteSyncRateLimiting:
    """execute_sync() must block when rate limit is saturated."""

    def test_blocks_when_limit_reached(self):
        # Allow only 2 requests per 0.5s window
        t = Throttler({"tiny": RateLimit(2, 0.5)})

        t.execute_sync("tiny", lambda: None)
        t.execute_sync("tiny", lambda: None)

        # Third call should block until window expires
        start = time.time()
        t.execute_sync("tiny", lambda: None)
        elapsed = time.time() - start

        # Should have waited ~0.5s (minus a bit for time already elapsed)
        assert elapsed >= 0.3, (
            f"execute_sync() should have blocked ~0.5s but only waited {elapsed:.3f}s"
        )
        assert t.get_stats("tiny")["total_waits"] >= 1


class TestDeadCodeRemoved:
    """Verify the orphaned duplicate code was removed from throttler.py."""

    def test_no_duplicate_exponential_backoff(self):
        """There should be exactly one ExponentialBackoff class in the module."""
        import core.net.throttler as mod
        import inspect

        classes = [
            name for name, obj in inspect.getmembers(mod, inspect.isclass)
            if name == "ExponentialBackoff"
        ]
        assert len(classes) == 1, (
            f"Expected 1 ExponentialBackoff class, found {len(classes)}"
        )

    def test_no_duplicate_create_combined(self):
        """There should be exactly one create_combined_throttler function."""
        import core.net.throttler as mod
        import inspect

        funcs = [
            name for name, obj in inspect.getmembers(mod, inspect.isfunction)
            if name == "create_combined_throttler"
        ]
        assert len(funcs) == 1, (
            f"Expected 1 create_combined_throttler function, found {len(funcs)}"
        )

    def test_file_line_count_reasonable(self):
        """After dead-code removal the file should be well under 400 lines."""
        import core.net.throttler as mod
        import inspect

        source = inspect.getsource(mod)
        lines = source.count("\n")
        assert lines < 400, (
            f"throttler.py has {lines} lines — dead code may not be fully removed"
        )
