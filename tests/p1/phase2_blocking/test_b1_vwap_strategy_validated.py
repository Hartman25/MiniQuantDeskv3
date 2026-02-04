"""
P2-B1 — VWAP Micro Mean Reversion Validated

INVARIANT:
    The VWAPMicroMeanReversion strategy MUST:
    1. Compute VWAP as sum(typical_price * volume) / sum(volume)
    2. Emit BUY signal only when bar.close < vwap * (1 - entry_deviation_pct)
    3. Emit SELL signal on mean reversion to VWAP (close >= vwap)
    4. Emit SELL signal on stop loss hit
    5. Emit SELL FORCE_FLAT near market close
    6. Only emit entry signals within trade window
    7. Respect max_trades_per_day
    8. Disable after daily_loss_limit hit
    9. Return None during warmup (bars < vwap_min_bars)
   10. Position size via risk-based formula: qty = risk_dollars / (price * stop_loss_pct)

TESTS:
    10 unit tests covering core VWAP behavior.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from core.data.contract import MarketDataContract

EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strat(**overrides) -> VWAPMicroMeanReversion:
    """Create strategy with sensible test defaults."""
    cfg = {
        "vwap_min_bars": 3,       # fast warmup for tests
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "daily_loss_limit_usd": "2.50",
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
        "flat_time": "15:55",
        "max_notional_usd": "500",  # high cap so sizing doesn't clip
    }
    cfg.update(overrides)
    return VWAPMicroMeanReversion(
        name="test_vwap",
        config=cfg,
        symbols=["SPY"],
        timeframe="1Min",
    )


def _bar(price: str, volume: int = 1000, hour: int = 10, minute: int = 15,
         day: int = 30, symbol: str = "SPY") -> MarketDataContract:
    """Create bar at given ET time. Price used for OHLC (flat bar)."""
    p = Decimal(price)
    et_time = datetime(2026, 1, day, hour, minute, 0, tzinfo=EASTERN)
    utc_time = et_time.astimezone(timezone.utc)
    return MarketDataContract(
        symbol=symbol,
        timestamp=utc_time,
        open=p,
        high=p,
        low=p,
        close=p,
        volume=volume,
        provider="test",
    )


def _warmup(strat, n: int = 3, price: str = "100.00", hour: int = 9, minute_start: int = 31):
    """Feed n bars to warm up VWAP. Returns last signal (should be None)."""
    sig = None
    for i in range(n):
        sig = strat.on_bar(_bar(price, hour=hour, minute=minute_start + i))
    return sig


# ===================================================================
# 1. VWAP calculation
# ===================================================================

class TestVWAPCalculation:

    def test_returns_none_during_warmup(self):
        """No signal before vwap_min_bars bars."""
        strat = _make_strat(vwap_min_bars=5)
        for i in range(4):
            sig = strat.on_bar(_bar("100.00", hour=10, minute=15 + i))
        assert sig is None

    def test_vwap_computed_after_warmup(self):
        """After warmup, VWAP state is populated."""
        strat = _make_strat(vwap_min_bars=3)
        for i in range(3):
            strat.on_bar(_bar("100.00", hour=9, minute=31 + i))
        # After 3 bars of flat 100.00, VWAP should be ~100.00
        assert strat._vwap.v_sum > 0
        assert strat._vwap.pv_sum > 0


# ===================================================================
# 2. Entry signal conditions
# ===================================================================

class TestEntrySignal:

    def test_buy_signal_on_deviation(self):
        """BUY emitted when close < vwap * (1 - entry_dev), inside trade window."""
        strat = _make_strat(vwap_min_bars=3, entry_deviation_pct="0.003")
        # Warmup at 100.00 (outside trade window)
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # Now drop price below VWAP threshold inside trade window
        # VWAP ≈ 100.00, threshold = 100 * (1 - 0.003) = 99.70
        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None
        assert sig.side == "BUY"
        assert sig.order_type == "LIMIT"
        assert sig.quantity > 0

    def test_no_signal_above_threshold(self):
        """No signal when price is above VWAP deviation threshold."""
        strat = _make_strat(vwap_min_bars=3, entry_deviation_pct="0.003")
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # Price at VWAP (no deviation)
        sig = strat.on_bar(_bar("100.00", hour=10, minute=15))
        assert sig is None

    def test_no_entry_outside_trade_window(self):
        """No BUY signal outside 10:00–11:30 window."""
        strat = _make_strat(vwap_min_bars=3)
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # 12:00 is outside window
        sig = strat.on_bar(_bar("99.60", hour=12, minute=0))
        assert sig is None


# ===================================================================
# 3. Exit signal conditions
# ===================================================================

class TestExitSignal:

    def test_sell_on_mean_reversion(self):
        """SELL emitted when close >= vwap while in position."""
        strat = _make_strat(vwap_min_bars=3)
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # Trigger entry
        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None and sig.side == "BUY"

        # Simulate fill
        strat.on_order_filled("ORD-1", "SPY", sig.quantity, Decimal("99.60"))

        # Price reverts to VWAP
        sig = strat.on_bar(_bar("100.00", hour=10, minute=20))
        assert sig is not None
        assert sig.side == "SELL"
        assert sig.reason == "MEAN_REVERSION_TO_VWAP"

    def test_sell_on_stop_loss(self):
        """SELL emitted when close <= entry * (1 - stop_loss_pct)."""
        strat = _make_strat(vwap_min_bars=3, stop_loss_pct="0.003")
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None
        strat.on_order_filled("ORD-2", "SPY", sig.quantity, Decimal("99.60"))

        # Drop below stop: 99.60 * (1 - 0.003) = 99.3012
        sig = strat.on_bar(_bar("99.29", hour=10, minute=20))
        assert sig is not None
        assert sig.side == "SELL"
        assert sig.reason == "STOP_LOSS"

    def test_force_flat_near_close(self):
        """SELL FORCE_FLAT emitted at flat_time (15:55)."""
        strat = _make_strat(vwap_min_bars=3)
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        strat.on_order_filled("ORD-3", "SPY", sig.quantity, Decimal("99.60"))

        # At 15:55 — force flat
        sig = strat.on_bar(_bar("99.80", hour=15, minute=55))
        assert sig is not None
        assert sig.side == "SELL"
        assert sig.reason == "FORCE_FLAT_EOD"


# ===================================================================
# 4. Guardrails
# ===================================================================

class TestGuardrails:

    def test_max_trades_per_day(self):
        """No more entries after max_trades_per_day reached."""
        strat = _make_strat(vwap_min_bars=3, max_trades_per_day=1)
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # First entry
        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None
        strat.on_order_filled("ORD-4", "SPY", sig.quantity, Decimal("99.60"))

        # Exit
        sig = strat.on_bar(_bar("100.00", hour=10, minute=20))
        assert sig is not None and sig.side == "SELL"
        strat.on_order_filled("ORD-5", "SPY", sig.quantity, Decimal("100.00"))

        # Second entry attempt — should be blocked
        sig = strat.on_bar(_bar("99.60", hour=10, minute=30))
        assert sig is None, "Should not enter after max_trades_per_day"

    def test_daily_loss_limit_disables(self):
        """Strategy disables for the day after daily loss limit hit."""
        strat = _make_strat(
            vwap_min_bars=3,
            daily_loss_limit_usd="1.50",
            risk_dollars_per_trade="1.50",
        )
        _warmup(strat, n=3, price="100.00", hour=9, minute_start=31)

        # Enter
        sig = strat.on_bar(_bar("99.60", hour=10, minute=15))
        assert sig is not None
        strat.on_order_filled("ORD-6", "SPY", sig.quantity, Decimal("99.60"))

        # Stop loss hit → daily_pnl_est drops by risk_dollars (1.50) >= limit (1.50)
        sig = strat.on_bar(_bar("99.29", hour=10, minute=20))
        assert sig is not None and sig.side == "SELL"

        # Now disabled
        assert strat._disabled_today is True

    def test_position_size_formula(self):
        """qty = risk_dollars / (price * stop_loss_pct), capped by max_notional."""
        strat = _make_strat(
            risk_dollars_per_trade="1.50",
            stop_loss_pct="0.003",
        )

        # qty = 1.50 / (100 * 0.003) = 1.50 / 0.30 = 5.0
        qty = strat._position_size(Decimal("100"))
        assert qty == Decimal("5.000")
