"""
Phase 2 — Offline Analytics Scaffolding Tests

Tests for time-of-day segmentation and parameter sensitivity.
These are offline-only — no live execution behavior is affected.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, time
from decimal import Decimal

import pytest

from core.analytics.performance import TradeResult
from strategies.offline.time_of_day import (
    TimeBucket,
    BucketPerformance,
    segment_by_time,
    DEFAULT_BUCKETS,
)
from strategies.offline.param_sensitivity import (
    ParameterPoint,
    SensitivityResult,
    evaluate_parameter_sensitivity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(entry_hour: int = 10, entry_min: int = 0, pnl: float = 1.0) -> TradeResult:
    base = datetime(2026, 1, 30, entry_hour, entry_min, 0, tzinfo=timezone.utc)
    return TradeResult(
        symbol="SPY",
        entry_time=base,
        exit_time=base + timedelta(minutes=30),
        entry_price=Decimal("100"),
        exit_price=Decimal("100") + Decimal(str(pnl)),
        quantity=Decimal("1"),
        side="LONG",
        pnl=Decimal(str(pnl)),
        pnl_percent=Decimal(str(pnl)),
        commission=Decimal("0"),
        duration_hours=0.5,
        strategy="VWAP",
    )


# ---------------------------------------------------------------------------
# Time-of-day segmentation
# ---------------------------------------------------------------------------

class TestTimeOfDaySegmentation:

    def test_empty_trades_returns_empty(self):
        result = segment_by_time([])
        assert result == []

    def test_single_trade_assigned_to_bucket(self):
        # 10:00 falls in "Morning" bucket (9:45-11:30)
        result = segment_by_time([_trade(entry_hour=10)])
        assert len(result) == 1
        assert result[0].bucket == "Morning"
        assert result[0].trade_count == 1

    def test_multiple_buckets(self):
        trades = [
            _trade(entry_hour=9, entry_min=35),   # Open-15min
            _trade(entry_hour=10),                  # Morning
            _trade(entry_hour=14, entry_min=30),   # Afternoon
        ]
        result = segment_by_time(trades)
        bucket_names = [r.bucket for r in result]
        assert "Open-15min" in bucket_names
        assert "Morning" in bucket_names
        assert "Afternoon" in bucket_names

    def test_bucket_pnl_aggregation(self):
        trades = [
            _trade(entry_hour=10, pnl=2.0),
            _trade(entry_hour=10, entry_min=15, pnl=-1.0),
        ]
        result = segment_by_time(trades)
        morning = [r for r in result if r.bucket == "Morning"][0]
        assert morning.trade_count == 2
        assert morning.total_pnl == Decimal("1.0")
        assert morning.avg_pnl == Decimal("0.5")

    def test_custom_buckets(self):
        custom = [TimeBucket("EarlyAM", time(6, 0), time(8, 0))]
        trades = [_trade(entry_hour=7)]
        result = segment_by_time(trades, buckets=custom)
        assert len(result) == 1
        assert result[0].bucket == "EarlyAM"

    def test_unmatched_trade_goes_to_other(self):
        custom = [TimeBucket("Narrow", time(10, 0), time(10, 30))]
        trades = [_trade(entry_hour=14)]  # doesn't match
        result = segment_by_time(trades, buckets=custom)
        assert result[0].bucket == "Other"

    def test_deterministic_ordering(self):
        """Results are sorted by bucket name."""
        trades = [
            _trade(entry_hour=14, entry_min=30),  # Afternoon
            _trade(entry_hour=10),                  # Morning
        ]
        result = segment_by_time(trades)
        assert result == sorted(result, key=lambda r: r.bucket)

    def test_win_rate_calculation(self):
        trades = [
            _trade(entry_hour=10, pnl=1.0),
            _trade(entry_hour=10, entry_min=5, pnl=-1.0),
            _trade(entry_hour=10, entry_min=10, pnl=0.5),
        ]
        result = segment_by_time(trades)
        morning = result[0]
        assert morning.win_rate == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Parameter sensitivity
# ---------------------------------------------------------------------------

class TestParameterSensitivity:

    def test_basic_sweep(self):
        """Sweep across 3 parameter values produces 3 points."""
        def fake_run(params):
            dev = float(params.get("entry_deviation_pct", 0.003))
            # Simulate: higher deviation = fewer but better trades
            pnl_per = Decimal(str(dev * 100))
            return [TradeResult(
                symbol="SPY",
                entry_time=datetime(2026, 1, 30, 10, 0, tzinfo=timezone.utc),
                exit_time=datetime(2026, 1, 30, 10, 30, tzinfo=timezone.utc),
                entry_price=Decimal("100"),
                exit_price=Decimal("100") + pnl_per,
                quantity=Decimal("1"),
                side="LONG",
                pnl=pnl_per,
                pnl_percent=pnl_per,
                commission=Decimal("0"),
                duration_hours=0.5,
                strategy="VWAP",
            )]

        result = evaluate_parameter_sensitivity(
            parameter_name="entry_deviation_pct",
            parameter_values=[0.002, 0.003, 0.005],
            run_func=fake_run,
        )
        assert isinstance(result, SensitivityResult)
        assert result.parameter_name == "entry_deviation_pct"
        assert len(result.points) == 3

    def test_empty_run_func(self):
        """run_func returning [] produces points with zero trades."""
        result = evaluate_parameter_sensitivity(
            parameter_name="x",
            parameter_values=[1, 2],
            run_func=lambda p: [],
        )
        assert len(result.points) == 2
        for pt in result.points:
            assert pt.trade_count == 0
            assert pt.total_pnl == Decimal("0")

    def test_point_has_required_fields(self):
        result = evaluate_parameter_sensitivity(
            parameter_name="x",
            parameter_values=[1],
            run_func=lambda p: [],
        )
        pt = result.points[0]
        assert isinstance(pt, ParameterPoint)
        assert hasattr(pt, "params")
        assert hasattr(pt, "trade_count")
        assert hasattr(pt, "total_pnl")
        assert hasattr(pt, "win_rate")
        assert hasattr(pt, "max_drawdown_pct")

    def test_base_params_preserved(self):
        """Base params are passed through to run_func with override."""
        captured = []

        def spy_run(params):
            captured.append(params)
            return []

        evaluate_parameter_sensitivity(
            parameter_name="x",
            parameter_values=[10],
            run_func=spy_run,
            base_params={"y": 20, "x": 0},
        )
        assert captured[0]["x"] == 10  # overridden
        assert captured[0]["y"] == 20  # preserved
