"""
Phase 2 — Signal vs Execution Attribution Tests

Invariants covered:
  P2-INV-14: Attribution by strategy/symbol/time bucket; deterministic ordering
  P2-INV-15: Signal vs execution timing; slippage
  P2-INV-16: Empty analyzer returns []
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from core.analytics.performance import TradeResult
from core.analytics.attribution import (
    AttributionBreakdown,
    TradeAttributionAnalyzer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(
    symbol: str = "SPY",
    strategy: str = "VWAP",
    signal_type: str = "BUY_DIP",
    pnl: float = 1.0,
    entry_hour: int = 10,
    signal_price: float = None,
    entry_price: float = 100.0,
    i: int = 0,
) -> TradeResult:
    base = datetime(2026, 1, 30, entry_hour, 0, 0, tzinfo=timezone.utc)
    return TradeResult(
        symbol=symbol,
        entry_time=base + timedelta(minutes=i),
        exit_time=base + timedelta(minutes=i + 30),
        entry_price=Decimal(str(entry_price)),
        exit_price=Decimal(str(entry_price)) + Decimal(str(pnl)),
        quantity=Decimal("1"),
        side="LONG",
        pnl=Decimal(str(pnl)),
        pnl_percent=Decimal(str(pnl)),
        commission=Decimal("0"),
        duration_hours=0.5,
        strategy=strategy,
        signal_type=signal_type,
        signal_time=base + timedelta(minutes=i - 1) if signal_price else None,
        signal_price=Decimal(str(signal_price)) if signal_price else None,
    )


# ---------------------------------------------------------------------------
# P2-INV-16: Empty analyzer
# ---------------------------------------------------------------------------

class TestEmptyAnalyzer:
    def test_empty_returns_empty_list_strategy(self):
        a = TradeAttributionAnalyzer()
        assert a.get_attribution_list("strategy") == []

    def test_empty_returns_empty_list_symbol(self):
        a = TradeAttributionAnalyzer()
        assert a.get_attribution_list("symbol") == []

    def test_empty_returns_empty_list_time(self):
        a = TradeAttributionAnalyzer()
        assert a.get_attribution_list("time_of_day") == []

    def test_empty_returns_empty_list_signal(self):
        a = TradeAttributionAnalyzer()
        assert a.get_attribution_list("signal_type") == []

    def test_empty_slippage_summary(self):
        a = TradeAttributionAnalyzer()
        s = a.get_slippage_summary()
        assert s["avg_entry_slippage"] == Decimal("0")
        assert s["trade_count_with_slippage"] == 0


# ---------------------------------------------------------------------------
# P2-INV-14: Attribution by dimension, deterministic ordering
# ---------------------------------------------------------------------------

class TestAttributionByDimension:
    def test_by_strategy(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(strategy="VWAP", pnl=2.0))
        a.add_trade(_trade(strategy="MOMENTUM", pnl=-1.0))
        a.add_trade(_trade(strategy="VWAP", pnl=1.0))

        result = a.get_attribution_list("strategy")
        assert isinstance(result, list)
        assert len(result) == 2

        # Deterministic ordering by subcategory name
        assert result[0].subcategory == "MOMENTUM"
        assert result[1].subcategory == "VWAP"

        # VWAP: 2 trades, total PnL = 3.0
        vwap = result[1]
        assert vwap.trade_count == 2
        assert vwap.total_pnl == Decimal("3.0")
        assert vwap.category == "strategy"

    def test_by_symbol(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(symbol="SPY", pnl=1.0))
        a.add_trade(_trade(symbol="QQQ", pnl=2.0))

        result = a.get_attribution_list("symbol")
        assert len(result) == 2
        assert result[0].subcategory == "QQQ"
        assert result[1].subcategory == "SPY"

    def test_by_signal_type(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(signal_type="BUY_DIP", pnl=1.0))
        a.add_trade(_trade(signal_type="BUY_BREAKOUT", pnl=-0.5))

        result = a.get_attribution_list("signal_type")
        assert len(result) == 2
        assert result[0].subcategory == "BUY_BREAKOUT"
        assert result[1].subcategory == "BUY_DIP"

    def test_unknown_dimension_returns_empty(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade())
        assert a.get_attribution_list("nonexistent") == []

    def test_breakdown_has_required_fields(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(pnl=1.0))
        a.add_trade(_trade(pnl=-0.5))

        result = a.get_attribution_list("strategy")
        assert len(result) == 1
        b = result[0]
        assert isinstance(b, AttributionBreakdown)
        assert hasattr(b, "category")
        assert hasattr(b, "subcategory")
        assert hasattr(b, "trade_count")
        assert hasattr(b, "total_pnl")
        assert hasattr(b, "avg_pnl_per_trade")
        assert hasattr(b, "win_rate")
        assert b.trade_count == 2
        assert b.winning_trades == 1
        assert b.losing_trades == 1


# ---------------------------------------------------------------------------
# P2-INV-15: Signal vs execution timing + slippage
# ---------------------------------------------------------------------------

class TestSignalVsExecution:
    def test_trade_result_has_signal_fields(self):
        t = _trade(signal_price=99.50, entry_price=100.0)
        assert t.signal_time is not None
        assert t.signal_price == Decimal("99.50")

    def test_entry_slippage_calculation(self):
        """entry_slippage = entry_price - signal_price."""
        t = _trade(signal_price=99.50, entry_price=100.0)
        assert t.entry_slippage == Decimal("0.50")

    def test_entry_slippage_none_when_no_signal_price(self):
        t = _trade(signal_price=None)
        assert t.entry_slippage is None

    def test_negative_slippage_is_favorable(self):
        """If filled below signal price, slippage is negative (favorable)."""
        t = _trade(signal_price=100.0, entry_price=99.80)
        assert t.entry_slippage == Decimal("-0.20")

    def test_slippage_summary(self):
        a = TradeAttributionAnalyzer()
        a.add_trade(_trade(signal_price=99.50, entry_price=100.0, i=0))  # slip=0.50
        a.add_trade(_trade(signal_price=100.0, entry_price=100.0, i=1))  # slip=0.00
        a.add_trade(_trade(signal_price=None, entry_price=100.0, i=2))   # no signal data

        s = a.get_slippage_summary()
        assert s["trade_count_with_slippage"] == 2
        assert s["avg_entry_slippage"] == Decimal("0.25")  # (0.50 + 0.00) / 2
        assert s["total_slippage_cost"] == Decimal("0.50")  # 0.50*1 + 0.00*1

    def test_to_dict_includes_signal_fields(self):
        t = _trade(signal_price=99.50, entry_price=100.0)
        d = t.to_dict()
        assert "signal_time" in d
        assert "signal_price" in d
        assert Decimal(d["signal_price"]) == Decimal("99.50")
        assert "entry_slippage" in d
        assert Decimal(d["entry_slippage"]) == Decimal("0.50")

    def test_to_dict_omits_signal_fields_when_none(self):
        t = _trade(signal_price=None)
        d = t.to_dict()
        assert "signal_time" not in d
        assert "signal_price" not in d
        assert "entry_slippage" not in d


# ---------------------------------------------------------------------------
# Time-of-day attribution
# ---------------------------------------------------------------------------

class TestTimeOfDayAttribution:
    def test_by_time_of_day_buckets(self):
        a = TradeAttributionAnalyzer()
        # Entry at 10:00 UTC = morning bucket
        a.add_trade(_trade(entry_hour=10, pnl=1.0, i=0))
        # Entry at 14:30 UTC = afternoon bucket
        a.add_trade(_trade(entry_hour=14, pnl=-0.5, i=30))

        result = a.get_attribution_list("time_of_day")
        assert len(result) >= 1  # at least one bucket matched
        for b in result:
            assert isinstance(b, AttributionBreakdown)
            assert b.category == "time_of_day"
