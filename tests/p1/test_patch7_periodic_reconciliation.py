"""
PATCH 7 — Periodic reconciliation, not just startup

INVARIANT:
    During the runtime loop, position and order state is compared to broker
    truth at a configurable interval (default 5 min).  Discrepancies are
    reported via ReconciliationResult.  The periodic reconciler gates on
    elapsed time so it never over-queries the broker.

TESTS:
    1. First call always runs.
    2. Second call within interval is skipped.
    3. Second call after interval runs.
    4. Discrepancies are passed through.
    5. No discrepancies → empty list.
    6. Reconciler error → synthetic discrepancy.
    7. Run count increments only on actual runs.
    8. Custom interval respected.
    9. Result carries correct timestamp.
   10. Thread safety: concurrent calls don't duplicate runs.
   11. PeriodicReconciler importable from module.
"""

import time
import threading
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.state.reconciler import (
    Discrepancy,
    PeriodicReconciler,
    ReconciliationResult,
    StartupReconciler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Injectable clock that returns a controllable timestamp."""
    def __init__(self, start: float = 1_000_000.0):
        self._ts = start

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._ts, tz=timezone.utc)

    def advance(self, seconds: float) -> None:
        self._ts += seconds


def _make_reconciler(discrepancies=None, *, raise_error=None):
    """Build a mock StartupReconciler."""
    mock = MagicMock(spec=StartupReconciler)
    if raise_error:
        mock.reconcile_startup.side_effect = raise_error
    else:
        mock.reconcile_startup.return_value = discrepancies or []
    return mock


def _sample_discrepancy(symbol="SPY", dtype="qty_mismatch"):
    return Discrepancy(
        type=dtype,
        symbol=symbol,
        local_value="10",
        broker_value="12",
        resolution="logged_only",
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPeriodicReconciler:

    def test_first_call_always_runs(self):
        """First call should always execute reconciliation."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        result = pr.check()
        assert result.ran is True
        assert inner.reconcile_startup.call_count == 1

    def test_second_call_within_interval_skipped(self):
        """If interval hasn't elapsed, check() returns ran=False."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        pr.check()
        clock.advance(100)  # only 100s, need 300
        result = pr.check()

        assert result.ran is False
        assert result.skipped_reason is not None
        assert "interval_not_elapsed" in result.skipped_reason
        assert inner.reconcile_startup.call_count == 1

    def test_second_call_after_interval_runs(self):
        """After interval elapses, check() runs again."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        pr.check()
        clock.advance(301)
        result = pr.check()

        assert result.ran is True
        assert inner.reconcile_startup.call_count == 2

    def test_discrepancies_passed_through(self):
        """Discrepancies from inner reconciler are in the result."""
        clock = FakeClock()
        discs = [_sample_discrepancy("AAPL"), _sample_discrepancy("TSLA")]
        inner = _make_reconciler(discs)
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        result = pr.check()
        assert result.ran is True
        assert len(result.discrepancies) == 2
        assert result.discrepancies[0].symbol == "AAPL"
        assert result.discrepancies[1].symbol == "TSLA"

    def test_no_discrepancies_empty_list(self):
        """No drift → empty discrepancy list."""
        clock = FakeClock()
        inner = _make_reconciler([])
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        result = pr.check()
        assert result.ran is True
        assert result.discrepancies == []

    def test_reconciler_error_yields_synthetic_discrepancy(self):
        """If inner reconciler raises, result contains a synthetic error discrepancy."""
        clock = FakeClock()
        inner = _make_reconciler(raise_error=RuntimeError("broker timeout"))
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        result = pr.check()
        assert result.ran is True
        assert len(result.discrepancies) == 1
        assert result.discrepancies[0].type == "reconciliation_error"
        assert "broker timeout" in str(result.discrepancies[0].broker_value)

    def test_run_count_increments_only_on_actual_runs(self):
        """run_count tracks actual reconciliation runs, not skipped calls."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        assert pr.run_count == 0

        pr.check()  # runs
        assert pr.run_count == 1

        clock.advance(10)
        pr.check()  # skipped
        assert pr.run_count == 1

        clock.advance(291)  # total 301
        pr.check()  # runs
        assert pr.run_count == 2

    def test_custom_interval_respected(self):
        """Short interval allows faster reconciliation cycles."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=10, clock=clock)

        pr.check()
        clock.advance(11)
        result = pr.check()
        assert result.ran is True
        assert pr.run_count == 2

    def test_result_has_timestamp(self):
        """ReconciliationResult carries a timestamp."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        result = pr.check()
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo is not None

    def test_thread_safety_no_duplicate_runs(self):
        """Concurrent calls should not cause duplicate reconciliation runs."""
        clock = FakeClock()
        call_count = {"n": 0}
        original_fn = MagicMock(return_value=[])

        def counting_reconcile():
            call_count["n"] += 1
            time.sleep(0.01)  # simulate work
            return original_fn()

        inner = _make_reconciler()
        inner.reconcile_startup.side_effect = counting_reconcile
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        # First call (runs)
        pr.check()

        # Advance past interval
        clock.advance(301)

        # Launch concurrent calls
        results = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(pr.check())

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one of the concurrent calls should have actually run
        ran_count = sum(1 for r in results if r.ran)
        assert ran_count == 1  # exactly one ran, rest skipped

    def test_interval_property(self):
        """interval_s is exposed as a read-only property."""
        clock = FakeClock()
        inner = _make_reconciler()
        pr = PeriodicReconciler(inner, interval_s=42, clock=clock)
        assert pr.interval_s == 42

    def test_skipped_result_has_empty_discrepancies(self):
        """Skipped runs return empty discrepancies list."""
        clock = FakeClock()
        inner = _make_reconciler([_sample_discrepancy()])
        pr = PeriodicReconciler(inner, interval_s=300, clock=clock)

        pr.check()  # runs, has discrepancies
        clock.advance(10)
        result = pr.check()  # skipped

        assert result.ran is False
        assert result.discrepancies == []
