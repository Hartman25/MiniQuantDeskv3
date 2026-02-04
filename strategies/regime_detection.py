"""
Deterministic regime detection heuristics.

These are pure functions for detecting adverse market regimes.
They use only bar data — no wall clock, no external feeds.

INVARIANT P2-INV-11: Known failure regimes documented + detectable.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class RegimeTag:
    """Result of a regime detection check."""
    regime: str       # e.g., "TREND_DAY", "VOL_SPIKE"
    detected: bool
    detail: str       # human-readable explanation
    metric: float     # quantitative metric (e.g., consecutive bars, ratio)


def detect_trend_day(
    closes: List[Decimal],
    vwaps: List[Decimal],
    threshold_bars: int = 15,
) -> RegimeTag:
    """
    Detect trend day regime.

    A trend day is signaled when `threshold_bars` consecutive bars have
    close < VWAP (persistent selling, mean reversion unlikely to work).

    Args:
        closes: List of recent bar close prices (chronological order).
        vwaps: List of VWAP values at each bar (same length as closes).
        threshold_bars: Number of consecutive bars below VWAP to trigger.

    Returns:
        RegimeTag with detected=True if regime identified.
    """
    if len(closes) != len(vwaps):
        return RegimeTag(
            regime="TREND_DAY",
            detected=False,
            detail="Input length mismatch",
            metric=0.0,
        )

    consecutive_below = 0
    max_consecutive = 0

    for close, vwap in zip(closes, vwaps):
        if close < vwap:
            consecutive_below += 1
            max_consecutive = max(max_consecutive, consecutive_below)
        else:
            consecutive_below = 0

    detected = max_consecutive >= threshold_bars
    return RegimeTag(
        regime="TREND_DAY",
        detected=detected,
        detail=f"{max_consecutive} consecutive bars below VWAP (threshold={threshold_bars})",
        metric=float(max_consecutive),
    )


def detect_volatility_spike(
    current_range: Decimal,
    rolling_avg_range: Decimal,
    threshold_ratio: float = 3.0,
) -> RegimeTag:
    """
    Detect volatility spike regime.

    A vol spike is signaled when the current bar range (high - low)
    exceeds `threshold_ratio` times the rolling average bar range.

    Args:
        current_range: Current bar (high - low).
        rolling_avg_range: Rolling average of (high - low) over recent bars.
        threshold_ratio: Multiple that triggers detection.

    Returns:
        RegimeTag with detected=True if spike identified.
    """
    if rolling_avg_range <= 0:
        return RegimeTag(
            regime="VOL_SPIKE",
            detected=False,
            detail="Rolling average range is zero or negative",
            metric=0.0,
        )

    ratio = float(current_range / rolling_avg_range)
    detected = ratio >= threshold_ratio

    return RegimeTag(
        regime="VOL_SPIKE",
        detected=detected,
        detail=f"Range ratio {ratio:.2f}x (threshold={threshold_ratio}x)",
        metric=ratio,
    )
