"""
Centralized NO-TRADE filter with structured reason codes.

DESIGN:
- Pure function: no side effects, no hidden state
- Returns a list of NoTradeReason (not a bool)
- Empty list = trade allowed
- Non-empty list = trade blocked; each element explains why

INVARIANTS:
  P2-INV-05: NO-TRADE reasons are first-class
  P2-INV-06: Session window enforcement
  P2-INV-07: Blackout near open/close
  P2-INV-08: Post-stop cooldown
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time, datetime
from enum import Enum, auto
from typing import List, Optional


class NoTradeReasonCode(Enum):
    """Enumeration of all no-trade reason codes."""
    OUTSIDE_SESSION = auto()
    BLACKOUT_NEAR_OPEN = auto()
    BLACKOUT_NEAR_CLOSE = auto()
    MAX_TRADES_REACHED = auto()
    DAILY_LOSS_LIMIT = auto()
    COOLDOWN_AFTER_STOP = auto()
    WARMUP_INCOMPLETE = auto()
    VOLATILITY_SPIKE = auto()
    REGIME_NOT_ALLOWED = auto()


@dataclass(frozen=True)
class NoTradeReason:
    """Structured reason why a trade was blocked."""
    code: NoTradeReasonCode
    message: str

    def __str__(self) -> str:
        return f"{self.code.name}: {self.message}"


@dataclass(frozen=True)
class NoTradeFilterConfig:
    """Configuration for NoTradeFilter. All fields have sensible defaults."""
    trade_start_time: time = time(10, 0)    # ET
    trade_end_time: time = time(11, 30)     # ET
    blackout_open_minutes: int = 5          # minutes after market open
    blackout_close_minutes: int = 5         # minutes before market close
    market_open_time: time = time(9, 30)    # ET
    market_close_time: time = time(16, 0)   # ET
    max_trades_per_day: int = 1
    daily_loss_limit_usd: float = 2.50
    cooldown_after_stop_bars: int = 0       # 0 = no cooldown


def check_no_trade(
    *,
    bar_time_et: time,
    trades_today: int,
    daily_pnl_est: float,
    bars_since_last_stop: Optional[int],
    warmup_complete: bool,
    config: NoTradeFilterConfig,
) -> List[NoTradeReason]:
    """
    Pure function: evaluate all no-trade conditions.

    Args:
        bar_time_et: Current bar time in US/Eastern.
        trades_today: Number of trades executed today.
        daily_pnl_est: Estimated daily P&L in USD (negative = loss).
        bars_since_last_stop: Bars since last stop-loss exit. None if no stop today.
        warmup_complete: Whether VWAP warmup has enough bars.
        config: Filter configuration.

    Returns:
        List of NoTradeReason. Empty = trade allowed.
    """
    reasons: List[NoTradeReason] = []

    # P2-INV-06: Session window enforcement
    if bar_time_et < config.trade_start_time or bar_time_et > config.trade_end_time:
        reasons.append(NoTradeReason(
            code=NoTradeReasonCode.OUTSIDE_SESSION,
            message=f"Bar time {bar_time_et} outside session {config.trade_start_time}-{config.trade_end_time}",
        ))

    # P2-INV-07: Blackout near market open
    if config.blackout_open_minutes > 0:
        open_h, open_m = config.market_open_time.hour, config.market_open_time.minute
        blackout_end_total = open_h * 60 + open_m + config.blackout_open_minutes
        bar_total = bar_time_et.hour * 60 + bar_time_et.minute
        open_total = open_h * 60 + open_m
        if open_total <= bar_total < blackout_end_total:
            reasons.append(NoTradeReason(
                code=NoTradeReasonCode.BLACKOUT_NEAR_OPEN,
                message=f"Within {config.blackout_open_minutes}min blackout after market open",
            ))

    # P2-INV-07: Blackout near market close
    if config.blackout_close_minutes > 0:
        close_h, close_m = config.market_close_time.hour, config.market_close_time.minute
        close_total = close_h * 60 + close_m
        blackout_start_total = close_total - config.blackout_close_minutes
        bar_total = bar_time_et.hour * 60 + bar_time_et.minute
        if blackout_start_total <= bar_total <= close_total:
            reasons.append(NoTradeReason(
                code=NoTradeReasonCode.BLACKOUT_NEAR_CLOSE,
                message=f"Within {config.blackout_close_minutes}min blackout before market close",
            ))

    # Max trades per day
    if trades_today >= config.max_trades_per_day:
        reasons.append(NoTradeReason(
            code=NoTradeReasonCode.MAX_TRADES_REACHED,
            message=f"Already {trades_today} trades today (max {config.max_trades_per_day})",
        ))

    # Daily loss limit
    if daily_pnl_est <= -abs(config.daily_loss_limit_usd):
        reasons.append(NoTradeReason(
            code=NoTradeReasonCode.DAILY_LOSS_LIMIT,
            message=f"Daily P&L ${daily_pnl_est:.2f} exceeds limit -${config.daily_loss_limit_usd:.2f}",
        ))

    # P2-INV-08: Post-stop cooldown
    if (config.cooldown_after_stop_bars > 0
            and bars_since_last_stop is not None
            and bars_since_last_stop < config.cooldown_after_stop_bars):
        reasons.append(NoTradeReason(
            code=NoTradeReasonCode.COOLDOWN_AFTER_STOP,
            message=f"{bars_since_last_stop} bars since stop (need {config.cooldown_after_stop_bars})",
        ))

    # Warmup incomplete
    if not warmup_complete:
        reasons.append(NoTradeReason(
            code=NoTradeReasonCode.WARMUP_INCOMPLETE,
            message="VWAP warmup not complete",
        ))

    return reasons
