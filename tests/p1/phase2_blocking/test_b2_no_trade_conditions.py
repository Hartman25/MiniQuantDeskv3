"""
P2-B2 — Explicit NO-TRADE Conditions

INVARIANT:
    The strategy MUST return None (no trade) when ANY of the following
    conditions is true.  Each condition is independently testable.

    1. Strategy disabled (enabled=False)
    2. Daily loss limit hit (_disabled_today=True)
    3. VWAP warmup incomplete (bars < vwap_min_bars)
    4. Outside trade window (before 10:00 or after 11:30 ET)
    5. Max trades per day reached
    6. Price NOT below VWAP deviation threshold
    7. Position size computes to zero (price=0 or extreme values)
    8. Already in position → no double entry (emits exit instead)

TESTS:
    8 explicit NO-TRADE tests, one per condition.
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
        "max_trades_per_day": 1,
        "daily_loss_limit_usd": "2.50",
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
        "flat_time": "15:55",
        "max_notional_usd": "500",
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


def _warmup(strat, n=3, price="100.00", hour=9, minute_start=31):
    for i in range(n):
        strat.on_bar(_bar(price, hour=hour, minute=minute_start + i))


class TestNoTradeConditions:

    def test_no_trade_when_disabled(self):
        """enabled=False → None on every bar."""
        strat = _make_strat()
        strat.enabled = False
        _warmup(strat)
        sig = strat.on_bar(_bar("99.60"))
        assert sig is None

    def test_no_trade_after_daily_loss_limit(self):
        """_disabled_today=True → None."""
        strat = _make_strat()
        _warmup(strat)
        strat._disabled_today = True
        sig = strat.on_bar(_bar("99.60"))
        assert sig is None

    def test_no_trade_during_warmup(self):
        """Before vwap_min_bars → None."""
        strat = _make_strat(vwap_min_bars=10)
        # Only 3 bars
        for i in range(3):
            sig = strat.on_bar(_bar("99.60", hour=10, minute=15 + i))
        assert sig is None

    def test_no_trade_before_window(self):
        """Before trade_start_time (10:00 ET) → None."""
        strat = _make_strat()
        _warmup(strat)
        sig = strat.on_bar(_bar("99.60", hour=9, minute=45))
        assert sig is None

    def test_no_trade_after_window(self):
        """After trade_end_time (11:30 ET) → None."""
        strat = _make_strat()
        _warmup(strat)
        sig = strat.on_bar(_bar("99.60", hour=12, minute=0))
        assert sig is None

    def test_no_trade_max_trades_reached(self):
        """After max_trades_per_day → None."""
        strat = _make_strat(max_trades_per_day=1)
        _warmup(strat)

        # First trade
        sig = strat.on_bar(_bar("99.60"))
        assert sig is not None
        strat.on_order_filled("O1", "SPY", sig.quantity, Decimal("99.60"))
        # Exit
        sig = strat.on_bar(_bar("100.00", minute=20))
        strat.on_order_filled("O2", "SPY", sig.quantity, Decimal("100.00"))

        # Second attempt → None
        sig = strat.on_bar(_bar("99.60", minute=30))
        assert sig is None

    def test_no_trade_price_above_threshold(self):
        """Price at or above VWAP → None (no deviation)."""
        strat = _make_strat()
        _warmup(strat, price="100.00")
        sig = strat.on_bar(_bar("100.00"))
        assert sig is None

    def test_no_trade_zero_position_size(self):
        """Price = 0 → position_size returns 0 → None."""
        strat = _make_strat()
        qty = strat._position_size(Decimal("0"))
        assert qty == Decimal("0")

    def test_no_double_entry_while_in_position(self):
        """While in position, strategy emits exit signals, not new entries."""
        strat = _make_strat()
        _warmup(strat)

        # Entry
        sig = strat.on_bar(_bar("99.60"))
        assert sig is not None and sig.side == "BUY"
        strat.on_order_filled("O3", "SPY", sig.quantity, Decimal("99.60"))

        # Another deviation bar while in position → should NOT be a BUY
        sig = strat.on_bar(_bar("99.50", minute=20))
        if sig is not None:
            assert sig.side == "SELL", "In-position signal must be SELL, not another BUY"
