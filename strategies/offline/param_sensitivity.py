"""
Parameter sensitivity scaffolding (offline analytics).

Provides a framework for evaluating how strategy parameters affect
performance metrics. This is a placeholder for future grid-search
or walk-forward optimization.

IMPORTANT: This module is OFFLINE ONLY. It must NOT affect live execution.
No actual optimization is performed — this is scaffolding for analytics.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Tuple

from core.analytics.performance import TradeResult


@dataclass(frozen=True)
class ParameterPoint:
    """A single point in parameter space."""
    params: Dict[str, Any]   # e.g., {"entry_deviation_pct": 0.003, "stop_loss_pct": 0.003}
    trade_count: int
    total_pnl: Decimal
    win_rate: float
    avg_pnl: Decimal
    max_drawdown_pct: Decimal


@dataclass(frozen=True)
class SensitivityResult:
    """Result of a parameter sensitivity analysis."""
    parameter_name: str
    points: List[ParameterPoint]  # sorted by parameter value


def evaluate_parameter_sensitivity(
    parameter_name: str,
    parameter_values: List[Any],
    run_func: Callable[[Dict[str, Any]], List[TradeResult]],
    base_params: Dict[str, Any] = None,
) -> SensitivityResult:
    """
    Evaluate performance across a range of parameter values.

    This is a scaffolding function. `run_func` must be provided by the caller
    (e.g., a backtest engine). This function only orchestrates the sweep.

    Args:
        parameter_name: Name of the parameter to vary.
        parameter_values: Values to test.
        run_func: Function that accepts params dict and returns TradeResult list.
        base_params: Base parameter set (parameter_name will be overridden).

    Returns:
        SensitivityResult with one ParameterPoint per value.
    """
    if base_params is None:
        base_params = {}

    points = []
    for val in parameter_values:
        params = {**base_params, parameter_name: val}
        trades = run_func(params)

        if not trades:
            points.append(ParameterPoint(
                params=params,
                trade_count=0,
                total_pnl=Decimal("0"),
                win_rate=0.0,
                avg_pnl=Decimal("0"),
                max_drawdown_pct=Decimal("0"),
            ))
            continue

        total_pnl = sum(t.pnl for t in trades)
        winners = sum(1 for t in trades if t.is_winner())

        # Simple drawdown
        equity = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for t in trades:
            equity += t.pnl
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak * Decimal("100")
                if dd > max_dd:
                    max_dd = dd

        points.append(ParameterPoint(
            params=params,
            trade_count=len(trades),
            total_pnl=total_pnl,
            win_rate=winners / len(trades),
            avg_pnl=total_pnl / len(trades),
            max_drawdown_pct=max_dd,
        ))

    return SensitivityResult(
        parameter_name=parameter_name,
        points=points,
    )
