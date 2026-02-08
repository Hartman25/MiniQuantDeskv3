"""
Strategy Performance Tracker (tests/p1 phase2 blocking)

This module is wired into core/strategies/__init__.py, which expects these names:
- StrategyPerformanceTracker
- StrategyPerformanceSnapshot
- StrategyPerformanceEvent
- StrategyPerformanceEventType
- StrategyStatus

Tests expect:
- StrategyPerformanceTracker ctor supports:
    min_sharpe_ratio: Decimal
    max_consecutive_losses: int
    min_win_rate_percent: Decimal
    max_drawdown_percent: Decimal
    min_trades_for_evaluation: int

- record_trade(...) signature with kwargs:
    strategy_id, symbol, side, quantity,
    entry_price, exit_price,
    entry_time, exit_time

- is_strategy_active(strategy_id) -> bool (unknown => True)
- disable_strategy(strategy_id) -> sets status[strategy_id] == DISABLED_MANUAL
- enable_strategy(strategy_id) -> sets ACTIVE + resets consecutive_losses[strategy_id] == 0

- Public dicts:
    status: dict[str, StrategyStatus]
    consecutive_losses: dict[str, int]

Auto-disable is only evaluated AFTER min_trades_for_evaluation trades.
Cutoffs:
- consecutive losses
- win rate %
- drawdown % (realized equity curve)
- sharpe ratio (simple per-trade pnl series)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import sqrt
from typing import Any, Dict, List, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(x: Any, default: Decimal = Decimal("0")) -> Decimal:
    if x is None:
        return default
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _pct(x: Decimal) -> Decimal:
    return x * Decimal("100")


class StrategyStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED_MANUAL = "DISABLED_MANUAL"
    DISABLED_PERFORMANCE = "DISABLED_PERFORMANCE"


class StrategyPerformanceEventType(str, Enum):
    TRADE_CLOSED = "TRADE_CLOSED"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"
    STRATEGY_ENABLED = "STRATEGY_ENABLED"


@dataclass(frozen=True)
class StrategyPerformanceEvent:
    strategy_id: str
    event_type: StrategyPerformanceEventType
    ts: datetime = field(default_factory=_utc_now)
    pnl: Optional[Decimal] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyPerformanceSnapshot:
    """
    Public snapshot object (imported by tests / __init__.py).
    """
    strategy_id: str
    status: StrategyStatus = StrategyStatus.ACTIVE

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0

    equity: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    trade_pnls: List[Decimal] = field(default_factory=list)

    def win_rate_percent(self) -> Decimal:
        if self.total_trades <= 0:
            return Decimal("0")
        return (Decimal(self.wins) / Decimal(self.total_trades)) * Decimal("100")

    def drawdown_percent(self) -> Decimal:
        if self.peak_equity <= Decimal("0"):
            return Decimal("0")
        dd = (self.peak_equity - self.equity) / self.peak_equity
        if dd < Decimal("0"):
            return Decimal("0")
        return _pct(dd)

    def sharpe_ratio(self) -> Decimal:
        """
        Simple per-trade Sharpe-like statistic:
          SR = mean / std * sqrt(n)
        Uses float math; returned as Decimal.
        """
        n = len(self.trade_pnls)
        if n < 2:
            return Decimal("0")

        vals = [float(p) for p in self.trade_pnls]
        mean = sum(vals) / float(n)

        var = sum((v - mean) ** 2 for v in vals) / float(n - 1)
        if var <= 0.0:
            return Decimal("0")

        sr = (mean / (var ** 0.5)) * sqrt(float(n))
        return _to_decimal(sr, default=Decimal("0"))


# Back-compat internal alias (in case anything refers to _Snapshot)
_Snapshot = StrategyPerformanceSnapshot


class StrategyPerformanceTracker:
    def __init__(
        self,
        *,
        min_sharpe_ratio: Decimal = Decimal("0.5"),
        max_consecutive_losses: int = 3,
        min_win_rate_percent: Decimal = Decimal("40.0"),
        max_drawdown_percent: Decimal = Decimal("15.0"),
        min_trades_for_evaluation: int = 5,
    ) -> None:
        self.min_sharpe_ratio = _to_decimal(min_sharpe_ratio, default=Decimal("0"))
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.min_win_rate_percent = _to_decimal(min_win_rate_percent, default=Decimal("0"))
        self.max_drawdown_percent = _to_decimal(max_drawdown_percent, default=Decimal("0"))
        self.min_trades_for_evaluation = int(min_trades_for_evaluation)

        self._snaps: Dict[str, StrategyPerformanceSnapshot] = {}
        self._events: List[StrategyPerformanceEvent] = []

        # PUBLIC dicts expected by tests
        self.status: Dict[str, StrategyStatus] = {}
        self.consecutive_losses: Dict[str, int] = {}

    # ---------------- public API ----------------

    def is_strategy_active(self, strategy_id: str) -> bool:
        """Unknown strategies are ACTIVE by default."""
        sid = (strategy_id or "UNKNOWN").strip()
        st = self.status.get(sid, StrategyStatus.ACTIVE)
        return st == StrategyStatus.ACTIVE

    def disable_strategy(self, strategy_id: str) -> None:
        """Manual disable."""
        sid = (strategy_id or "UNKNOWN").strip()
        snap = self._get_snap(sid)
        snap.status = StrategyStatus.DISABLED_MANUAL
        self.status[sid] = snap.status
        self.consecutive_losses[sid] = snap.consecutive_losses

        self._events.append(
            StrategyPerformanceEvent(
                strategy_id=sid,
                event_type=StrategyPerformanceEventType.STRATEGY_DISABLED,
                meta={"mode": "manual"},
            )
        )

    def enable_strategy(self, strategy_id: str) -> None:
        """Manual enable (reactivate) and reset consecutive-loss counter as tests require."""
        sid = (strategy_id or "UNKNOWN").strip()
        snap = self._get_snap(sid)
        snap.status = StrategyStatus.ACTIVE
        snap.consecutive_losses = 0

        self.status[sid] = snap.status
        self.consecutive_losses[sid] = 0

        self._events.append(
            StrategyPerformanceEvent(
                strategy_id=sid,
                event_type=StrategyPerformanceEventType.STRATEGY_ENABLED,
                meta={"mode": "manual"},
            )
        )

    def list_events(self) -> List[StrategyPerformanceEvent]:
        return list(self._events)

    def get_snapshot(self, strategy_id: str) -> StrategyPerformanceSnapshot:
        """Convenience accessor (safe for unknown strategies)."""
        sid = (strategy_id or "UNKNOWN").strip()
        return self._get_snap(sid)

    # ---------------- trade recording ----------------

    def record_trade(
        self,
        *,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Any,
        entry_price: Any,
        exit_price: Any,
        entry_time: Optional[datetime] = None,
        exit_time: Optional[datetime] = None,
    ) -> None:
        """
        Record a CLOSED trade and compute realized pnl.

        side expected by tests: "LONG" (we also accept "SHORT")
        pnl for LONG  = (exit - entry) * qty
        pnl for SHORT = (entry - exit) * qty
        """
        sid = (strategy_id or "UNKNOWN").strip()
        snap = self._get_snap(sid)

        qty = _to_decimal(quantity, default=Decimal("0"))
        ep = _to_decimal(entry_price, default=Decimal("0"))
        xp = _to_decimal(exit_price, default=Decimal("0"))
        s = (side or "LONG").strip().upper()

        if qty == 0:
            pnl = Decimal("0")
        else:
            pnl = (ep - xp) * qty if s == "SHORT" else (xp - ep) * qty

        self._events.append(
            StrategyPerformanceEvent(
                strategy_id=sid,
                event_type=StrategyPerformanceEventType.TRADE_CLOSED,
                ts=exit_time or _utc_now(),
                pnl=pnl,
                meta={
                    "symbol": symbol,
                    "side": s,
                    "quantity": str(qty),
                    "entry_price": str(ep),
                    "exit_price": str(xp),
                    "entry_time": entry_time.isoformat() if entry_time else None,
                    "exit_time": exit_time.isoformat() if exit_time else None,
                },
            )
        )

        # Ensure public dicts initialized
        self.status.setdefault(sid, snap.status)
        self.consecutive_losses.setdefault(sid, snap.consecutive_losses)

        # If disabled, ignore further stat updates (stable behavior)
        if snap.status != StrategyStatus.ACTIVE:
            return

        # Update stats
        snap.total_trades += 1
        snap.trade_pnls.append(pnl)

        snap.equity += pnl
        if snap.equity > snap.peak_equity:
            snap.peak_equity = snap.equity

        if pnl > 0:
            snap.wins += 1
            snap.consecutive_losses = 0
        elif pnl < 0:
            snap.losses += 1
            snap.consecutive_losses += 1

        # reflect into public dicts
        self.consecutive_losses[sid] = snap.consecutive_losses

        self._evaluate_and_maybe_disable(sid)

    # ---------------- internals ----------------

    def _get_snap(self, sid: str) -> StrategyPerformanceSnapshot:
        if sid not in self._snaps:
            self._snaps[sid] = StrategyPerformanceSnapshot(strategy_id=sid)
            self.status[sid] = StrategyStatus.ACTIVE
            self.consecutive_losses[sid] = 0
        return self._snaps[sid]

    def _auto_disable(self, sid: str, reason: str) -> None:
        snap = self._get_snap(sid)
        snap.status = StrategyStatus.DISABLED_PERFORMANCE
        self.status[sid] = snap.status

        self._events.append(
            StrategyPerformanceEvent(
                strategy_id=sid,
                event_type=StrategyPerformanceEventType.STRATEGY_DISABLED,
                meta={"mode": "performance", "reason": reason},
            )
        )

    def _evaluate_and_maybe_disable(self, sid: str) -> None:
        snap = self._get_snap(sid)
        if snap.status != StrategyStatus.ACTIVE:
            return

        # Guard: do not evaluate before minimum trades
        if snap.total_trades < self.min_trades_for_evaluation:
            return

        # 1) consecutive losses
        if self.max_consecutive_losses > 0 and snap.consecutive_losses >= self.max_consecutive_losses:
            self._auto_disable(sid, f"max_consecutive_losses={self.max_consecutive_losses}")
            return

        # 2) win rate
        if snap.win_rate_percent() < self.min_win_rate_percent:
            self._auto_disable(sid, f"min_win_rate_percent={self.min_win_rate_percent}")
            return

        # 3) drawdown
        if snap.drawdown_percent() > self.max_drawdown_percent:
            self._auto_disable(sid, f"max_drawdown_percent={self.max_drawdown_percent}")
            return

        # 4) sharpe
        if snap.sharpe_ratio() < self.min_sharpe_ratio:
            self._auto_disable(sid, f"min_sharpe_ratio={self.min_sharpe_ratio}")
            return
