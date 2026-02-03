"""
Strategy retirement evaluator.

DESIGN:
- Pure function evaluation: deterministic based on rolling TradeResult window
- No side effects, no hidden state
- Returns RetirementDecision with reason + timestamp

INVARIANTS:
  P2-INV-12: Strategy retired when rolling expectancy < threshold OR rolling drawdown > threshold
  P2-INV-13: Retired strategy may still emit exit signals for existing positions
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import List, Optional

from core.analytics.performance import TradeResult


class RetirementReasonCode(Enum):
    """Why a strategy was retired."""
    LOW_EXPECTANCY = auto()
    EXCESSIVE_DRAWDOWN = auto()


@dataclass(frozen=True)
class RetirementDecision:
    """Result of retirement evaluation."""
    retired: bool
    retirement_reason: Optional[RetirementReasonCode] = None
    detail: str = ""
    evaluated_at: Optional[datetime] = None


@dataclass(frozen=True)
class RetirementConfig:
    """Configuration for retirement evaluation."""
    rolling_window: int = 20                     # number of recent trades to evaluate
    min_trades_for_evaluation: int = 10          # don't retire until at least this many trades
    expectancy_threshold: Decimal = Decimal("-0.50")  # retire if avg PnL per trade below this
    max_drawdown_pct: Decimal = Decimal("15.0")  # retire if rolling drawdown exceeds this %


def evaluate_retirement(
    trades: List[TradeResult],
    config: RetirementConfig,
    evaluated_at: Optional[datetime] = None,
) -> RetirementDecision:
    """
    Evaluate whether a strategy should be retired based on rolling results.

    Pure function: no side effects. Uses only the provided trade list.

    Args:
        trades: Recent trade results (most recent last).
        config: Retirement thresholds.
        evaluated_at: Timestamp for the decision (informational only).

    Returns:
        RetirementDecision with retired=True if thresholds breached.
    """
    # Not enough data to evaluate
    if len(trades) < config.min_trades_for_evaluation:
        return RetirementDecision(
            retired=False,
            detail=f"Only {len(trades)} trades (need {config.min_trades_for_evaluation})",
            evaluated_at=evaluated_at,
        )

    # Use last `rolling_window` trades
    window = trades[-config.rolling_window:]

    # --- Rolling expectancy check ---
    total_pnl = sum(t.pnl for t in window)
    avg_pnl = total_pnl / len(window)

    if avg_pnl < config.expectancy_threshold:
        return RetirementDecision(
            retired=True,
            retirement_reason=RetirementReasonCode.LOW_EXPECTANCY,
            detail=f"Rolling expectancy ${avg_pnl:.4f} < threshold ${config.expectancy_threshold}",
            evaluated_at=evaluated_at,
        )

    # --- Rolling drawdown check ---
    # Compute equity curve from trade PnLs and find max drawdown
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd_pct = Decimal("0")

    for t in window:
        equity += t.pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd_pct = (peak - equity) / peak * Decimal("100")
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

    if max_dd_pct > config.max_drawdown_pct:
        return RetirementDecision(
            retired=True,
            retirement_reason=RetirementReasonCode.EXCESSIVE_DRAWDOWN,
            detail=f"Rolling drawdown {max_dd_pct:.2f}% > threshold {config.max_drawdown_pct}%",
            evaluated_at=evaluated_at,
        )

    return RetirementDecision(
        retired=False,
        detail=f"OK: expectancy ${avg_pnl:.4f}, max_dd {max_dd_pct:.2f}%",
        evaluated_at=evaluated_at,
    )
