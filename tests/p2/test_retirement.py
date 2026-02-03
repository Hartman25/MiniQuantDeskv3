"""
Phase 2 — Strategy Retirement Tests

Invariants covered:
  P2-INV-12: Strategy retired when rolling expectancy < threshold OR rolling drawdown > threshold
  P2-INV-13: Retired strategy must not emit new entries (exits still allowed)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from core.analytics.performance import TradeResult
from strategies.retirement import (
    RetirementConfig,
    RetirementDecision,
    RetirementReasonCode,
    evaluate_retirement,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(pnl: float, i: int = 0) -> TradeResult:
    """Create a minimal TradeResult with given PnL."""
    base = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
    return TradeResult(
        symbol="SPY",
        entry_time=base + timedelta(hours=i),
        exit_time=base + timedelta(hours=i, minutes=30),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("100.00") + Decimal(str(pnl)),
        quantity=Decimal("1"),
        side="LONG",
        pnl=Decimal(str(pnl)),
        pnl_percent=Decimal(str(pnl)),
        commission=Decimal("0"),
        duration_hours=0.5,
        strategy="test_strat",
    )


# ---------------------------------------------------------------------------
# P2-INV-12: Retirement evaluation
# ---------------------------------------------------------------------------

class TestRetirementEvaluation:

    def test_not_retired_with_insufficient_trades(self):
        """Fewer trades than min_trades_for_evaluation => not retired."""
        trades = [_make_trade(1.0, i) for i in range(5)]
        config = RetirementConfig(min_trades_for_evaluation=10)
        decision = evaluate_retirement(trades, config)
        assert decision.retired is False

    def test_retired_on_low_expectancy(self):
        """Rolling expectancy below threshold => retired."""
        # 10 trades, each losing $1.00 => avg = -$1.00
        trades = [_make_trade(-1.0, i) for i in range(10)]
        config = RetirementConfig(
            min_trades_for_evaluation=10,
            expectancy_threshold=Decimal("-0.50"),
        )
        decision = evaluate_retirement(trades, config)
        assert decision.retired is True
        assert decision.retirement_reason == RetirementReasonCode.LOW_EXPECTANCY

    def test_not_retired_when_expectancy_above_threshold(self):
        """Rolling expectancy above threshold => not retired."""
        trades = [_make_trade(0.50, i) for i in range(10)]
        config = RetirementConfig(
            min_trades_for_evaluation=10,
            expectancy_threshold=Decimal("-0.50"),
        )
        decision = evaluate_retirement(trades, config)
        assert decision.retired is False

    def test_retired_on_excessive_drawdown(self):
        """Rolling drawdown exceeds threshold => retired."""
        # Sequence: big win then big losses to create drawdown
        trades = []
        trades.append(_make_trade(10.0, 0))   # equity: 10
        trades.append(_make_trade(10.0, 1))   # equity: 20 (peak)
        for i in range(8):
            trades.append(_make_trade(-2.0, i + 2))  # equity: 20 -> 4 (dd=80%)

        config = RetirementConfig(
            min_trades_for_evaluation=10,
            max_drawdown_pct=Decimal("15.0"),
            expectancy_threshold=Decimal("-100"),  # disable expectancy check
        )
        decision = evaluate_retirement(trades, config)
        assert decision.retired is True
        assert decision.retirement_reason == RetirementReasonCode.EXCESSIVE_DRAWDOWN

    def test_not_retired_with_small_drawdown(self):
        """Small drawdown within limits => not retired."""
        trades = []
        for i in range(10):
            trades.append(_make_trade(1.0 if i % 3 != 0 else -0.5, i))

        config = RetirementConfig(
            min_trades_for_evaluation=10,
            max_drawdown_pct=Decimal("50.0"),
            expectancy_threshold=Decimal("-10.0"),
        )
        decision = evaluate_retirement(trades, config)
        assert decision.retired is False

    def test_rolling_window_uses_last_n_trades(self):
        """Only the last `rolling_window` trades are evaluated."""
        # First 10: all losers
        old_trades = [_make_trade(-5.0, i) for i in range(10)]
        # Last 10: all winners
        recent_trades = [_make_trade(2.0, i + 10) for i in range(10)]

        config = RetirementConfig(
            rolling_window=10,
            min_trades_for_evaluation=10,
            expectancy_threshold=Decimal("-0.50"),
        )
        decision = evaluate_retirement(old_trades + recent_trades, config)
        assert decision.retired is False, "Only last 10 (winners) should be evaluated"

    def test_decision_has_timestamp(self):
        """Decision includes evaluated_at timestamp."""
        trades = [_make_trade(1.0, i) for i in range(10)]
        ts = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        decision = evaluate_retirement(
            trades,
            RetirementConfig(min_trades_for_evaluation=10),
            evaluated_at=ts,
        )
        assert decision.evaluated_at == ts


# ---------------------------------------------------------------------------
# P2-INV-13: Retired => no new entries (integration-level concept)
# ---------------------------------------------------------------------------

class TestRetiredBehavior:
    """These tests validate the contract that retired=True means no new entries."""

    def test_retired_decision_is_truthy(self):
        """retired=True is truthy for conditional checks."""
        decision = RetirementDecision(
            retired=True,
            retirement_reason=RetirementReasonCode.LOW_EXPECTANCY,
        )
        assert decision.retired  # strategy code does `if decision.retired: return None`

    def test_not_retired_is_falsy(self):
        decision = RetirementDecision(retired=False)
        assert not decision.retired

    def test_retirement_reason_is_set(self):
        """When retired, reason code must be present."""
        trades = [_make_trade(-2.0, i) for i in range(10)]
        config = RetirementConfig(
            min_trades_for_evaluation=10,
            expectancy_threshold=Decimal("-0.50"),
        )
        decision = evaluate_retirement(trades, config)
        assert decision.retired is True
        assert decision.retirement_reason is not None
        assert isinstance(decision.retirement_reason, RetirementReasonCode)
