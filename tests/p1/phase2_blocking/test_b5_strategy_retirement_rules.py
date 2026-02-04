"""
P2-B5 — Strategy Retirement Rules

INVARIANT:
    The StrategyPerformanceTracker MUST auto-disable strategies when:
    1. Consecutive losses >= max_consecutive_losses threshold
    2. Win rate < min_win_rate_percent (after min_trades)
    3. Sharpe ratio < min_sharpe_ratio (after min_trades)

    Strategies MUST NOT be evaluated until min_trades_for_evaluation
    trades have been recorded.

    Manual disable/enable MUST work independently of auto-cutoff.

TESTS:
    7 tests covering auto-cutoff triggers, min-trades guard, and
    manual overrides.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.strategies.performance_tracker import (
    StrategyPerformanceTracker,
    StrategyStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker(**overrides) -> StrategyPerformanceTracker:
    defaults = dict(
        min_sharpe_ratio=Decimal("0.5"),
        max_consecutive_losses=3,
        min_win_rate_percent=Decimal("40.0"),
        max_drawdown_percent=Decimal("15.0"),
        min_trades_for_evaluation=5,
    )
    defaults.update(overrides)
    return StrategyPerformanceTracker(**defaults)


def _record_trade(tracker, strategy_id="strat", pnl_positive=True, idx=0):
    """Record a winning or losing trade with slight variation to avoid zero-variance Sharpe."""
    base_time = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
    entry_time = base_time + timedelta(minutes=idx * 10)
    exit_time = entry_time + timedelta(minutes=5)

    entry_price = Decimal("100.00")
    # Add small variation to exit price to avoid zero-variance Sharpe
    variation = Decimal(str(idx * 0.01))
    if pnl_positive:
        exit_price = Decimal("101.00") + variation
    else:
        exit_price = Decimal("99.00") - variation

    tracker.record_trade(
        strategy_id=strategy_id,
        symbol="SPY",
        side="LONG",
        quantity=Decimal("10"),
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=entry_time,
        exit_time=exit_time,
    )


# ===================================================================
# 1. Consecutive losses auto-disable
# ===================================================================

class TestConsecutiveLossCutoff:

    def test_disabled_after_consecutive_losses(self):
        """3 consecutive losses (after min_trades) → disabled."""
        t = _tracker(max_consecutive_losses=3, min_trades_for_evaluation=5)

        # 5 winning trades to pass min_trades
        for i in range(5):
            _record_trade(t, pnl_positive=True, idx=i)
        assert t.is_strategy_active("strat")

        # 3 consecutive losses → disabled
        for i in range(3):
            _record_trade(t, pnl_positive=False, idx=5 + i)

        assert not t.is_strategy_active("strat")
        assert t.status["strat"] == StrategyStatus.DISABLED_PERFORMANCE

    def test_win_resets_consecutive_counter(self):
        """A win resets consecutive loss counter; no disable."""
        # Use lenient thresholds so only consecutive losses matter
        t = _tracker(
            max_consecutive_losses=3,
            min_trades_for_evaluation=5,
            min_sharpe_ratio=Decimal("-999"),
            min_win_rate_percent=Decimal("0"),
            max_drawdown_percent=Decimal("999"),
        )

        for i in range(5):
            _record_trade(t, pnl_positive=True, idx=i)

        # 2 losses then a win → no disable
        _record_trade(t, pnl_positive=False, idx=5)
        _record_trade(t, pnl_positive=False, idx=6)
        _record_trade(t, pnl_positive=True, idx=7)

        assert t.is_strategy_active("strat")
        assert t.consecutive_losses["strat"] == 0


# ===================================================================
# 2. Win rate cutoff
# ===================================================================

class TestWinRateCutoff:

    def test_low_win_rate_disables(self):
        """Win rate below threshold → disabled after min_trades."""
        t = _tracker(min_win_rate_percent=Decimal("40.0"), min_trades_for_evaluation=5)

        # 1 win, 4 losses = 20% win rate
        _record_trade(t, pnl_positive=True, idx=0)
        for i in range(4):
            _record_trade(t, pnl_positive=False, idx=1 + i)

        assert not t.is_strategy_active("strat")


# ===================================================================
# 3. Min trades guard
# ===================================================================

class TestMinTradesGuard:

    def test_no_evaluation_before_min_trades(self):
        """Strategy stays active even with all losses if < min_trades."""
        t = _tracker(
            max_consecutive_losses=3,
            min_trades_for_evaluation=10,
        )

        # 3 consecutive losses but only 3 trades (< 10 min)
        for i in range(3):
            _record_trade(t, pnl_positive=False, idx=i)

        assert t.is_strategy_active("strat"), (
            "Should not disable before min_trades threshold"
        )


# ===================================================================
# 4. Manual disable/enable
# ===================================================================

class TestManualOverride:

    def test_manual_disable(self):
        """disable_strategy() → not active."""
        t = _tracker()
        _record_trade(t, pnl_positive=True, idx=0)
        t.disable_strategy("strat")
        assert not t.is_strategy_active("strat")
        assert t.status["strat"] == StrategyStatus.DISABLED_MANUAL

    def test_manual_enable_after_auto_disable(self):
        """enable_strategy() re-activates after auto-disable."""
        t = _tracker(max_consecutive_losses=3, min_trades_for_evaluation=5)

        # Trigger auto-disable
        for i in range(5):
            _record_trade(t, pnl_positive=True, idx=i)
        for i in range(3):
            _record_trade(t, pnl_positive=False, idx=5 + i)
        assert not t.is_strategy_active("strat")

        # Re-enable
        t.enable_strategy("strat")
        assert t.is_strategy_active("strat")
        assert t.consecutive_losses["strat"] == 0

    def test_is_active_default(self):
        """Unknown strategy is considered active by default."""
        t = _tracker()
        assert t.is_strategy_active("unknown_strat") is True
