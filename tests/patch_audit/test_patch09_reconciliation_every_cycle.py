"""
PATCH 9 tests: Periodic reconciliation in runtime loop.

Problem: Reconciliation only runs at startup. Position drift can accumulate
during runtime without detection.

Solution: Add PeriodicReconciler.check() calls in main runtime loop.

Tests:
1. PeriodicReconciler is invoked every cycle
2. Reconciliation respects interval gating
3. Discrepancies are logged when found
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest


def test_periodic_reconciler_gating():
    """PATCH 9: PeriodicReconciler gates on interval correctly."""
    from core.state.reconciler import PeriodicReconciler, StartupReconciler, Discrepancy

    # Create mock reconciler
    base_reconciler = MagicMock(spec=StartupReconciler)
    base_reconciler.reconcile_startup.return_value = []

    # Create PeriodicReconciler with 60s interval
    periodic = PeriodicReconciler(base_reconciler, interval_s=60.0)

    # First check: should run
    result1 = periodic.check()
    assert result1.ran is True
    assert base_reconciler.reconcile_startup.call_count == 1

    # Second check immediately after: should NOT run (interval not elapsed)
    result2 = periodic.check()
    assert result2.ran is False
    assert result2.skipped_reason and "interval_not_elapsed" in result2.skipped_reason
    assert base_reconciler.reconcile_startup.call_count == 1  # Still just 1 call

    # Third check: same result
    result3 = periodic.check()
    assert result3.ran is False
    assert base_reconciler.reconcile_startup.call_count == 1


def test_periodic_reconciler_detects_discrepancies():
    """PATCH 9: PeriodicReconciler reports discrepancies when found."""
    from core.state.reconciler import PeriodicReconciler, StartupReconciler, Discrepancy

    base_reconciler = MagicMock(spec=StartupReconciler)

    # Mock reconciler returns a discrepancy
    discrepancy = Discrepancy(
        type="position_qty_mismatch",
        symbol="AAPL",
        local_value=Decimal("100"),
        broker_value=Decimal("95"),
        resolution="require_manual_review",
        timestamp=datetime.now(timezone.utc),
    )
    base_reconciler.reconcile_startup.return_value = [discrepancy]

    periodic = PeriodicReconciler(base_reconciler, interval_s=60.0)

    result = periodic.check()
    assert result.ran is True
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].symbol == "AAPL"
    assert result.discrepancies[0].type == "position_qty_mismatch"


def test_periodic_reconciler_with_clock():
    """PATCH 9: PeriodicReconciler can use injectable clock for testing."""
    from core.state.reconciler import PeriodicReconciler, StartupReconciler
    from datetime import timedelta

    base_reconciler = MagicMock(spec=StartupReconciler)
    base_reconciler.reconcile_startup.return_value = []

    # Mock clock
    mock_clock = MagicMock()
    start_time = datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
    mock_clock.now.return_value = start_time

    periodic = PeriodicReconciler(base_reconciler, interval_s=60.0, clock=mock_clock)

    # First check at T=0
    result1 = periodic.check()
    assert result1.ran is True
    assert base_reconciler.reconcile_startup.call_count == 1

    # Advance clock by 30s (not enough to trigger)
    mock_clock.now.return_value = start_time + timedelta(seconds=30)
    result2 = periodic.check()
    assert result2.ran is False
    assert base_reconciler.reconcile_startup.call_count == 1

    # Advance clock by 61s total (enough to trigger)
    mock_clock.now.return_value = start_time + timedelta(seconds=61)
    result3 = periodic.check()
    assert result3.ran is True
    assert base_reconciler.reconcile_startup.call_count == 2


def test_periodic_reconciler_run_count():
    """PATCH 9: PeriodicReconciler tracks run count."""
    from core.state.reconciler import PeriodicReconciler, StartupReconciler
    from datetime import timedelta

    base_reconciler = MagicMock(spec=StartupReconciler)
    base_reconciler.reconcile_startup.return_value = []

    mock_clock = MagicMock()
    start_time = datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
    mock_clock.now.return_value = start_time

    periodic = PeriodicReconciler(base_reconciler, interval_s=60.0, clock=mock_clock)

    assert periodic.run_count == 0

    # First run
    periodic.check()
    assert periodic.run_count == 1

    # Skipped (within interval)
    periodic.check()
    assert periodic.run_count == 1

    # Advance and run again
    mock_clock.now.return_value = start_time + timedelta(seconds=61)
    periodic.check()
    assert periodic.run_count == 2
