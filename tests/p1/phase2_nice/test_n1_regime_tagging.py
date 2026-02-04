"""
P2-N1 — Regime Tagging

INVARIANT:
    Every StrategySignal emitted by VWAPMicroMeanReversion MUST carry
    a non-empty `reason` field that tags the market regime / trigger.

    Known reason tags:
      - PRICE_BELOW_VWAP_BY_* (entry)
      - MEAN_REVERSION_TO_VWAP (exit)
      - STOP_LOSS (exit)
      - FORCE_FLAT_EOD (exit)
      - MAX_TIME_IN_TRADE (exit)

TESTS:
    5 tests — one per signal type, verifying reason is non-empty and
    matches the expected tag pattern.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from core.data.contract import MarketDataContract

EASTERN = ZoneInfo("America/New_York")


def _strat(**kw):
    cfg = {"vwap_min_bars": 3, "entry_deviation_pct": "0.003",
           "stop_loss_pct": "0.003", "risk_dollars_per_trade": "1.50",
           "max_trades_per_day": 2, "daily_loss_limit_usd": "10",
           "trade_start_time": "10:00", "trade_end_time": "14:00",
           "flat_time": "15:55", "max_notional_usd": "500",
           "max_time_in_trade_minutes": 30}
    cfg.update(kw)
    return VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])


def _bar(price, hour=10, minute=15, day=30):
    p = Decimal(str(price))
    et = datetime(2026, 1, day, hour, minute, 0, tzinfo=EASTERN)
    return MarketDataContract(symbol="SPY", timestamp=et.astimezone(timezone.utc),
                              open=p, high=p, low=p, close=p, volume=1000, provider="t")


def _warmup(s, n=3):
    for i in range(n):
        s.on_bar(_bar(100, hour=9, minute=31 + i))


class TestReasonTagging:

    def test_entry_has_vwap_reason(self):
        s = _strat()
        _warmup(s)
        sig = s.on_bar(_bar(99.60))
        assert sig is not None
        assert "VWAP" in sig.reason

    def test_mean_reversion_exit_reason(self):
        s = _strat()
        _warmup(s)
        sig = s.on_bar(_bar(99.60))
        s.on_order_filled("O1", "SPY", sig.quantity, Decimal("99.60"))
        sig = s.on_bar(_bar(100, minute=20))
        assert sig is not None
        assert sig.reason == "MEAN_REVERSION_TO_VWAP"

    def test_stop_loss_exit_reason(self):
        s = _strat()
        _warmup(s)
        sig = s.on_bar(_bar(99.60))
        s.on_order_filled("O2", "SPY", sig.quantity, Decimal("99.60"))
        sig = s.on_bar(_bar(99.29, minute=20))
        assert sig is not None
        assert sig.reason == "STOP_LOSS"

    def test_force_flat_reason(self):
        s = _strat()
        _warmup(s)
        sig = s.on_bar(_bar(99.60))
        s.on_order_filled("O3", "SPY", sig.quantity, Decimal("99.60"))
        sig = s.on_bar(_bar(99.70, hour=15, minute=55))
        assert sig is not None
        assert sig.reason == "FORCE_FLAT_EOD"

    def test_max_time_exit_reason(self):
        s = _strat(max_time_in_trade_minutes=30)
        _warmup(s)
        sig = s.on_bar(_bar(99.60))
        s.on_order_filled("O4", "SPY", sig.quantity, Decimal("99.60"))
        sig = s.on_bar(_bar(99.70, hour=10, minute=46))
        assert sig is not None
        assert sig.reason == "MAX_TIME_IN_TRADE"
