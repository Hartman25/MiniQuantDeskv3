"""
Phase 2 — Failure Regime Detection Tests

Invariant covered:
  P2-INV-11: Known failure regimes documented + at least 1-2 heuristics tested.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from strategies.regime_detection import (
    detect_trend_day,
    detect_volatility_spike,
    RegimeTag,
)


# ---------------------------------------------------------------------------
# Trend Day Detection
# ---------------------------------------------------------------------------

class TestTrendDayDetection:
    def test_persistent_below_vwap_detected(self):
        """15+ consecutive bars below VWAP => trend day detected."""
        closes = [Decimal("99.00")] * 20
        vwaps = [Decimal("100.00")] * 20
        tag = detect_trend_day(closes, vwaps, threshold_bars=15)
        assert tag.detected is True
        assert tag.regime == "TREND_DAY"
        assert tag.metric >= 15

    def test_normal_reversion_not_detected(self):
        """Bars that alternate above/below VWAP => no trend day."""
        closes = [Decimal("99.50"), Decimal("100.50")] * 10  # alternating
        vwaps = [Decimal("100.00")] * 20
        tag = detect_trend_day(closes, vwaps, threshold_bars=15)
        assert tag.detected is False
        assert tag.metric <= 1

    def test_exactly_at_threshold(self):
        """Exactly threshold_bars consecutive below => detected."""
        closes = [Decimal("99.00")] * 15
        vwaps = [Decimal("100.00")] * 15
        tag = detect_trend_day(closes, vwaps, threshold_bars=15)
        assert tag.detected is True

    def test_below_threshold(self):
        """14 consecutive below (threshold 15) => not detected."""
        closes = [Decimal("99.00")] * 14
        vwaps = [Decimal("100.00")] * 14
        tag = detect_trend_day(closes, vwaps, threshold_bars=15)
        assert tag.detected is False

    def test_empty_input(self):
        """Empty lists => not detected."""
        tag = detect_trend_day([], [], threshold_bars=15)
        assert tag.detected is False

    def test_mismatched_lengths(self):
        """Mismatched input lengths => not detected (defensive)."""
        tag = detect_trend_day(
            [Decimal("99")] * 5,
            [Decimal("100")] * 3,
            threshold_bars=3,
        )
        assert tag.detected is False


# ---------------------------------------------------------------------------
# Volatility Spike Detection
# ---------------------------------------------------------------------------

class TestVolatilitySpikeDetection:
    def test_large_range_detected(self):
        """Current range 4x average => spike detected."""
        tag = detect_volatility_spike(
            current_range=Decimal("2.00"),
            rolling_avg_range=Decimal("0.50"),
            threshold_ratio=3.0,
        )
        assert tag.detected is True
        assert tag.regime == "VOL_SPIKE"
        assert tag.metric >= 3.0

    def test_normal_range_not_detected(self):
        """Current range 1.5x average => not a spike."""
        tag = detect_volatility_spike(
            current_range=Decimal("0.75"),
            rolling_avg_range=Decimal("0.50"),
            threshold_ratio=3.0,
        )
        assert tag.detected is False
        assert tag.metric < 3.0

    def test_exactly_at_threshold(self):
        """Range ratio exactly 3.0 => detected."""
        tag = detect_volatility_spike(
            current_range=Decimal("1.50"),
            rolling_avg_range=Decimal("0.50"),
            threshold_ratio=3.0,
        )
        assert tag.detected is True

    def test_zero_average_range(self):
        """Zero average range => not detected (no divide-by-zero)."""
        tag = detect_volatility_spike(
            current_range=Decimal("1.00"),
            rolling_avg_range=Decimal("0"),
            threshold_ratio=3.0,
        )
        assert tag.detected is False

    def test_returns_regime_tag_type(self):
        """Return type is RegimeTag."""
        tag = detect_volatility_spike(
            current_range=Decimal("1.00"),
            rolling_avg_range=Decimal("0.50"),
        )
        assert isinstance(tag, RegimeTag)
        assert hasattr(tag, "regime")
        assert hasattr(tag, "detected")
        assert hasattr(tag, "detail")
        assert hasattr(tag, "metric")
