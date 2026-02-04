"""
P2-N2 — Signal vs Execution Attribution

INVARIANT:
    The SlippageAnalyzer MUST track expected vs actual fill prices
    and compute slippage in basis points.

    The TradeAttributionAnalyzer MUST break down P&L by strategy,
    signal type, time of day, and symbol.

TESTS:
    6 tests covering slippage recording and attribution breakdown.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.analytics.slippage import SlippageRecord, SlippageAnalyzer
from core.analytics.attribution import TradeAttributionAnalyzer
from core.analytics.performance import TradeResult


class TestSlippageTracking:

    def test_slippage_record_stores_bps(self):
        """SlippageRecord stores slippage in basis points."""
        rec = SlippageRecord(
            symbol="SPY", timestamp=datetime.now(timezone.utc),
            side="BUY", expected_price=Decimal("100.00"),
            actual_price=Decimal("100.10"), quantity=Decimal("10"),
            slippage_dollars=Decimal("1.00"),
            slippage_bps=10,
            time_to_fill_ms=500, order_type="MARKET",
        )
        assert rec.slippage_bps == 10
        assert rec.slippage_dollars == Decimal("1.00")

    def test_analyzer_record_execution(self):
        """record_execution() stores records in .records list."""
        analyzer = SlippageAnalyzer()
        analyzer.record_execution(
            symbol="SPY", side="BUY",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("100.05"),
            quantity=Decimal("10"),
            time_to_fill_ms=200,
            order_type="MARKET",
        )
        assert len(analyzer.records) >= 1
        assert analyzer.records[-1].symbol == "SPY"

    def test_slippage_bps_positive_for_adverse_buy(self):
        """BUY slippage: positive bps when actual > expected."""
        analyzer = SlippageAnalyzer()
        analyzer.record_execution(
            symbol="SPY", side="BUY",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("100.10"),
            quantity=Decimal("10"),
            time_to_fill_ms=100,
            order_type="MARKET",
        )
        rec = analyzer.records[-1]
        assert rec.slippage_bps > 0


class TestTradeAttribution:

    def _make_result(self, strategy="vwap", signal_type="BUY_DEVIATION",
                     pnl=Decimal("1.00"), hour=10):
        t = datetime(2026, 1, 30, hour, 15, 0, tzinfo=timezone.utc)
        return TradeResult(
            symbol="SPY",
            strategy=strategy,
            signal_type=signal_type,
            side="LONG",
            entry_price=Decimal("100.00"),
            exit_price=Decimal("100.00") + pnl / Decimal("10"),
            quantity=Decimal("10"),
            pnl=pnl,
            pnl_percent=pnl,
            commission=Decimal("0"),
            duration_hours=0.5,
            entry_time=t,
            exit_time=t + timedelta(minutes=30),
        )

    def test_attribution_by_strategy(self):
        """P&L is broken down by strategy."""
        analyzer = TradeAttributionAnalyzer()
        analyzer.add_trade(self._make_result(strategy="vwap", pnl=Decimal("2.00")))
        analyzer.add_trade(self._make_result(strategy="momentum", pnl=Decimal("-1.00")))

        by_strat = analyzer.get_attribution_by_strategy()
        names = [b.subcategory for b in by_strat.values()]
        assert "vwap" in names
        assert "momentum" in names

    def test_attribution_by_time_of_day(self):
        """P&L is broken down by time-of-day buckets."""
        analyzer = TradeAttributionAnalyzer()
        analyzer.add_trade(self._make_result(hour=10))
        analyzer.add_trade(self._make_result(hour=14))

        by_tod = analyzer.get_attribution_by_time_of_day()
        assert len(by_tod) >= 1

    def test_attribution_by_symbol(self):
        """P&L is broken down by symbol."""
        analyzer = TradeAttributionAnalyzer()
        analyzer.add_trade(self._make_result())

        by_sym = analyzer.get_attribution_by_symbol()
        symbols = [b.subcategory for b in by_sym.values()]
        assert "SPY" in symbols
