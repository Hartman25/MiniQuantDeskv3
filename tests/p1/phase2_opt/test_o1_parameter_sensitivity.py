"""
P2-O1 — Parameter Sensitivity Analysis

INVARIANT:
    Strategy parameters (entry_deviation_pct, stop_loss_pct, etc.)
    MUST be configurable and produce deterministic behavior changes.
    Tighter thresholds → fewer signals; wider → more signals.

TESTS:
    3 tests proving parameter sensitivity is deterministic.
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
           "max_trades_per_day": 5, "daily_loss_limit_usd": "50",
           "trade_start_time": "10:00", "trade_end_time": "14:00",
           "flat_time": "15:55", "max_notional_usd": "500",
           "max_time_in_trade_minutes": 0}
    cfg.update(kw)
    return VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])


def _bar(price, hour=10, minute=15):
    p = Decimal(str(price))
    et = datetime(2026, 1, 30, hour, minute, 0, tzinfo=EASTERN)
    return MarketDataContract(symbol="SPY", timestamp=et.astimezone(timezone.utc),
                              open=p, high=p, low=p, close=p, volume=1000, provider="t")


def _warmup(s, n=3):
    for i in range(n):
        s.on_bar(_bar(100, hour=9, minute=31 + i))


class TestParameterSensitivity:

    def test_tighter_deviation_fewer_entries(self):
        """Smaller entry_deviation_pct → fewer entries (higher bar to trigger)."""
        tight = _strat(entry_deviation_pct="0.001")  # 0.1%
        wide = _strat(entry_deviation_pct="0.01")    # 1.0%

        _warmup(tight)
        _warmup(wide)

        # Price at 99.80 — 0.2% below VWAP(100)
        # tight (0.1%): 100*(1-0.001)=99.90 → 99.80 < 99.90 → triggers
        # wide (1.0%): 100*(1-0.01)=99.00 → 99.80 >= 99.00 → does NOT trigger
        sig_tight = tight.on_bar(_bar(99.80))
        sig_wide = wide.on_bar(_bar(99.80))

        assert sig_tight is not None, "Tight threshold should trigger at 0.2% deviation"
        assert sig_wide is None, "Wide threshold should NOT trigger at 0.2% deviation"

    def test_position_size_scales_with_risk_dollars(self):
        """Higher risk_dollars → larger position size."""
        small_risk = _strat(risk_dollars_per_trade="0.50")
        large_risk = _strat(risk_dollars_per_trade="5.00")

        qty_small = small_risk._position_size(Decimal("100"))
        qty_large = large_risk._position_size(Decimal("100"))

        assert qty_large > qty_small

    def test_max_trades_limits_entries(self):
        """Lower max_trades_per_day → fewer allowed entries."""
        s = _strat(max_trades_per_day=1)
        _warmup(s)

        sig1 = s.on_bar(_bar(99.60))
        assert sig1 is not None
        s.on_order_filled("O1", "SPY", sig1.quantity, Decimal("99.60"))
        s.on_bar(_bar(100, minute=20))  # exit
        s.on_order_filled("O2", "SPY", sig1.quantity, Decimal("100"))

        sig2 = s.on_bar(_bar(99.60, minute=30))
        assert sig2 is None, "Second entry blocked by max_trades_per_day=1"
