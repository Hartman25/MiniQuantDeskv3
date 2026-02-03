"""
Time-of-day performance segmentation (offline analytics).

Breaks trade results into configurable time buckets and computes
per-bucket performance metrics. Useful for identifying which parts
of the trading session are profitable vs. costly.

IMPORTANT: This module is OFFLINE ONLY. It must NOT affect live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from typing import Dict, List, Tuple

from core.analytics.performance import TradeResult


@dataclass(frozen=True)
class TimeBucket:
    """A named time-of-day bucket."""
    name: str
    start: time  # inclusive
    end: time    # exclusive


@dataclass(frozen=True)
class BucketPerformance:
    """Performance summary for one time bucket."""
    bucket: str
    trade_count: int
    total_pnl: Decimal
    avg_pnl: Decimal
    win_rate: float
    avg_duration_hours: float


# Default intraday buckets (ET)
DEFAULT_BUCKETS: List[TimeBucket] = [
    TimeBucket("Pre-Open", time(4, 0), time(9, 30)),
    TimeBucket("Open-15min", time(9, 30), time(9, 45)),
    TimeBucket("Morning", time(9, 45), time(11, 30)),
    TimeBucket("Midday", time(11, 30), time(14, 0)),
    TimeBucket("Afternoon", time(14, 0), time(15, 45)),
    TimeBucket("Close-15min", time(15, 45), time(16, 0)),
]


def segment_by_time(
    trades: List[TradeResult],
    buckets: List[TimeBucket] = None,
) -> List[BucketPerformance]:
    """
    Segment trade results by time-of-day bucket.

    Uses entry_time.time() to assign each trade to a bucket.
    Trades not matching any bucket are placed in "Other".

    Args:
        trades: Completed trade results.
        buckets: Time buckets (defaults to DEFAULT_BUCKETS).

    Returns:
        List of BucketPerformance sorted by bucket name.
    """
    if buckets is None:
        buckets = DEFAULT_BUCKETS

    grouped: Dict[str, List[TradeResult]] = {b.name: [] for b in buckets}
    grouped["Other"] = []

    for t in trades:
        entry_t = t.entry_time.time()
        placed = False
        for b in buckets:
            if b.start <= entry_t < b.end:
                grouped[b.name].append(t)
                placed = True
                break
        if not placed:
            grouped["Other"].append(t)

    results = []
    for name, bucket_trades in sorted(grouped.items()):
        if not bucket_trades:
            continue
        total_pnl = sum(t.pnl for t in bucket_trades)
        winners = sum(1 for t in bucket_trades if t.is_winner())
        results.append(BucketPerformance(
            bucket=name,
            trade_count=len(bucket_trades),
            total_pnl=total_pnl,
            avg_pnl=total_pnl / len(bucket_trades),
            win_rate=winners / len(bucket_trades) if bucket_trades else 0.0,
            avg_duration_hours=(
                sum(t.duration_hours for t in bucket_trades) / len(bucket_trades)
            ),
        ))

    return results
