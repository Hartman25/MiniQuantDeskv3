"""
P2-O2 — Time-of-Day Performance Segmentation

INVARIANT:
    The TradeAttributionAnalyzer MUST segment trade results into
    time-of-day buckets and produce per-bucket P&L metrics.

TESTS:
    3 tests proving time segmentation works.
"""

from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.analytics.attribution import TradeAttributionAnalyzer
from core.analytics.performance import TradeResult


def _trade(hour, pnl=Decimal("1.00")):
    t = datetime(2026, 1, 30, hour, 15, 0, tzinfo=timezone.utc)
    return TradeResult(
        symbol="SPY", strategy="vwap", signal_type="BUY",
        side="LONG", entry_price=Decimal("100"),
        exit_price=Decimal("100") + pnl / 10, quantity=Decimal("10"),
        pnl=pnl, pnl_percent=pnl, commission=Decimal("0"),
        duration_hours=0.5, entry_time=t, exit_time=t + timedelta(minutes=30),
    )


class TestTimeOfDaySegmentation:

    def test_buckets_populated(self):
        """Multiple time buckets appear in attribution."""
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(10))  # Morning
        a.add_trade(_trade(14))  # Afternoon
        buckets = a.get_attribution_by_time_of_day()
        assert len(buckets) >= 2

    def test_bucket_pnl_correct(self):
        """Each bucket's total_pnl matches the trades assigned to it."""
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(10, Decimal("2.00")))
        a.add_trade(_trade(10, Decimal("3.00")))
        buckets = a.get_attribution_by_time_of_day()
        # Find the bucket containing hour 10
        morning = [b for b in buckets.values() if b.trade_count == 2]
        assert len(morning) == 1
        assert morning[0].total_pnl == Decimal("5.00")

    def test_empty_analyzer_returns_empty(self):
        """No trades → empty attribution dict."""
        a = TradeAttributionAnalyzer()
        assert a.get_attribution_by_time_of_day() == {}
