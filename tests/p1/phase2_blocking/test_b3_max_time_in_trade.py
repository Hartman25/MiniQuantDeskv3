"""
P2-B3 — Max Time-in-Trade Enforcement

INVARIANT:
    When in position, if the bar timestamp is more than
    max_time_in_trade_minutes after the entry time, the strategy
    MUST emit a SELL signal with reason "MAX_TIME_IN_TRADE".

    Default max_time_in_trade_minutes = 60 (configurable).
    If max_time_in_trade_minutes is 0 or None, the feature is disabled.

TESTS:
    1. After max time → SELL MAX_TIME_IN_TRADE emitted
    2. Before max time → no forced exit (normal behavior)
    3. max_time_in_trade_minutes=0 → feature disabled, no forced exit
    4. Force-flat EOD takes precedence over max-time (both would fire)
    5. Entry time tracking: _entry_time is set on fill
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from core.data.contract import MarketDataContract

EASTERN = ZoneInfo("America/New_York")


def _make_strat(**overrides) -> VWAPMicroMeanReversion:
    cfg = {
        "vwap_min_bars": 3,
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 2,
        "daily_loss_limit_usd": "10.00",
        "trade_start_time": "10:00",
        "trade_end_time": "14:00",
        "flat_time": "15:55",
        "max_notional_usd": "500",
        "max_time_in_trade_minutes": 60,  # NEW config key
    }
    cfg.update(overrides)
    return VWAPMicroMeanReversion(
        name="test_vwap", config=cfg, symbols=["SPY"], timeframe="1Min",
    )


def _bar(price: str, volume: int = 1000, hour: int = 10, minute: int = 15,
         day: int = 30) -> MarketDataContract:
    p = Decimal(price)
    et = datetime(2026, 1, day, hour, minute, 0, tzinfo=EASTERN)
    return MarketDataContract(
        symbol="SPY", timestamp=et.astimezone(timezone.utc),
        open=p, high=p, low=p, close=p, volume=volume, provider="test",
    )


def _warmup(strat, n=3, price="100.00"):
    for i in range(n):
        strat.on_bar(_bar(price, hour=9, minute=31 + i))


class TestMaxTimeInTrade:

    def test_forced_exit_after_max_time(self):
        """After 60 minutes in trade → SELL MAX_TIME_IN_TRADE."""
        strat = _make_strat(max_time_in_trade_minutes=60)
        _warmup(strat)

        # Enter at 10:15
        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None and sig.side == "BUY"
        strat.on_order_filled("O1", "SPY", sig.quantity, Decimal("99.60"))

        # 60+ minutes later at 11:16 — still below VWAP, no stop hit,
        # but max time exceeded
        sig = strat.on_bar(_bar("99.70", hour=11, minute=16))
        assert sig is not None, "Should emit forced exit after max time"
        assert sig.side == "SELL"
        assert sig.reason == "MAX_TIME_IN_TRADE"

    def test_no_forced_exit_before_max_time(self):
        """Before 60 minutes → no forced time exit."""
        strat = _make_strat(max_time_in_trade_minutes=60)
        _warmup(strat)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        strat.on_order_filled("O2", "SPY", sig.quantity, Decimal("99.60"))

        # 30 minutes later — within max time
        sig = strat.on_bar(_bar("99.70", hour=10, minute=45))
        # Should be None (price between stop and VWAP, not timed out)
        assert sig is None

    def test_disabled_when_zero(self):
        """max_time_in_trade_minutes=0 → no forced time exit."""
        strat = _make_strat(max_time_in_trade_minutes=0)
        _warmup(strat)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        strat.on_order_filled("O3", "SPY", sig.quantity, Decimal("99.60"))

        # 2+ hours later — no max time check
        sig = strat.on_bar(_bar("99.70", hour=12, minute=30))
        assert sig is None, "Should not force exit when feature disabled"

    def test_entry_time_tracked(self):
        """_entry_time is set when fill received."""
        strat = _make_strat()
        _warmup(strat)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert strat._entry_time is None, "No entry time before fill"

        strat.on_order_filled("O4", "SPY", sig.quantity, Decimal("99.60"))
        assert strat._entry_time is not None, "Entry time set after fill"

    def test_entry_time_cleared_on_exit(self):
        """_entry_time is cleared when position exits."""
        strat = _make_strat()
        _warmup(strat)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        strat.on_order_filled("O5", "SPY", sig.quantity, Decimal("99.60"))

        # Exit via mean reversion
        sig = strat.on_bar(_bar("100.00", hour=10, minute=20))
        assert sig is not None and sig.side == "SELL"
        strat.on_order_filled("O6", "SPY", sig.quantity, Decimal("100.00"))

        assert strat._entry_time is None, "Entry time cleared after exit"
