"""
Phase 2 — VWAP Micro Mean Reversion Correctness Tests

Invariants covered:
  P2-INV-01: LONG-only enforcement
  P2-INV-02: VWAP reset on new day
  P2-INV-03: Entry only below deviation
  P2-INV-04: No entry within deviation
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from tests.p2.conftest import make_bar, make_strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _warmup(strat, n: int = 5, base_price: Decimal = Decimal("100.00"), day: datetime = None):
    """Feed n bars to warm up VWAP. Returns the VWAP-equivalent price (approx)."""
    if day is None:
        day = datetime(2026, 1, 30, 14, 0, 0, tzinfo=timezone.utc)  # 09:00 ET

    for i in range(n):
        ts = day + timedelta(minutes=i)
        bar = make_bar(
            timestamp=ts,
            open_=base_price,
            high=base_price + Decimal("0.05"),
            low=base_price - Decimal("0.05"),
            close=base_price,
            volume=1000,
        )
        strat.on_bar(bar)
    return base_price  # approx VWAP when all bars same price


# ---------------------------------------------------------------------------
# P2-INV-01: LONG-only enforcement
# ---------------------------------------------------------------------------

class TestLongOnly:
    """VWAPMicroMeanReversion must never emit BUY when not entering, or SELL when not in position."""

    def test_no_sell_signal_without_position(self):
        """SELL signal must not be emitted when strategy has no open position."""
        strat = make_strategy({"vwap_min_bars": 3})
        # Warm up within trade window (10:00-11:30 ET = 15:00-16:30 UTC in winter)
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)  # 10:00 ET

        for i in range(10):
            bar = make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            )
            sig = strat.on_bar(bar)
            if sig is not None:
                assert sig.side == "BUY", f"Without position, only BUY allowed, got {sig.side}"

    def test_entry_signal_is_always_buy(self):
        """Entry signals must always be BUY (LONG-only)."""
        strat = make_strategy({"vwap_min_bars": 3})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        # Warm up with normal price
        for i in range(3):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        # Now send bar well below VWAP to trigger entry
        entry_bar = make_bar(
            timestamp=base_ts + timedelta(minutes=5),
            close=Decimal("99.00"),  # well below VWAP of ~100
            high=Decimal("99.10"),
            low=Decimal("98.90"),
            open_=Decimal("99.00"),
            volume=1000,
        )
        sig = strat.on_bar(entry_bar)
        assert sig is not None, "Expected entry signal below deviation"
        assert sig.side == "BUY", f"Entry must be BUY, got {sig.side}"


# ---------------------------------------------------------------------------
# P2-INV-02: VWAP reset on new day
# ---------------------------------------------------------------------------

class TestVWAPReset:
    """VWAP state must reset when the trading day changes."""

    def test_vwap_resets_on_new_trading_day(self):
        """After day boundary, bars_today resets to 0 and VWAP re-warms."""
        strat = make_strategy({"vwap_min_bars": 3})

        # Day 1: warm up
        day1_base = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)  # Thursday
        for i in range(5):
            strat.on_bar(make_bar(
                timestamp=day1_base + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        assert strat._bars_today == 5, "Should have 5 bars on day 1"

        # Day 2: first bar
        day2_base = datetime(2026, 2, 2, 15, 0, 0, tzinfo=timezone.utc)  # Monday
        strat.on_bar(make_bar(
            timestamp=day2_base,
            close=Decimal("101.00"),
            high=Decimal("101.05"),
            low=Decimal("100.95"),
            open_=Decimal("101.00"),
            volume=1000,
        ))

        assert strat._bars_today == 1, "bars_today should reset to 1 on new day"
        assert strat._trades_today == 0, "trades_today should reset on new day"
        assert strat._disabled_today is False, "disabled should reset on new day"

    def test_vwap_no_signal_during_warmup_after_reset(self):
        """After day reset, no signal emitted until warmup bars re-accumulated."""
        strat = make_strategy({"vwap_min_bars": 5})

        # Day 1: full warmup
        day1_base = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            strat.on_bar(make_bar(
                timestamp=day1_base + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        # Day 2: only 2 bars (below warmup threshold of 5)
        day2_base = datetime(2026, 2, 2, 15, 0, 0, tzinfo=timezone.utc)
        for i in range(2):
            sig = strat.on_bar(make_bar(
                timestamp=day2_base + timedelta(minutes=i),
                close=Decimal("90.00"),  # far below any VWAP — would trigger if warmed up
                high=Decimal("90.05"),
                low=Decimal("89.95"),
                open_=Decimal("90.00"),
                volume=1000,
            ))
            assert sig is None, f"No signal during warmup, got {sig} on bar {i}"


# ---------------------------------------------------------------------------
# P2-INV-03: Entry only below deviation
# ---------------------------------------------------------------------------

class TestEntryBelowDeviation:
    """Entry signal must only fire when price < VWAP * (1 - entry_deviation_pct)."""

    def test_entry_triggers_below_threshold(self):
        """Price significantly below VWAP should trigger entry."""
        strat = make_strategy({"vwap_min_bars": 3, "entry_deviation_pct": "0.01"})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)  # 10:00 ET

        # Warm up at 100.00
        for i in range(3):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        # VWAP ~ 100.00, threshold ~ 99.00 (1% below)
        # Send bar at 98.50 — well below threshold
        sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=5),
            close=Decimal("98.50"),
            high=Decimal("98.60"),
            low=Decimal("98.40"),
            open_=Decimal("98.50"),
            volume=1000,
        ))
        assert sig is not None, "Expected BUY signal below deviation threshold"
        assert sig.side == "BUY"


# ---------------------------------------------------------------------------
# P2-INV-04: No entry within deviation
# ---------------------------------------------------------------------------

class TestNoEntryWithinDeviation:
    """No entry when price is within the deviation band."""

    def test_no_entry_at_vwap(self):
        """Price at VWAP should not trigger entry."""
        strat = make_strategy({"vwap_min_bars": 3, "entry_deviation_pct": "0.003"})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        # Price at VWAP — within deviation
        sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=5),
            close=Decimal("100.00"),
            high=Decimal("100.05"),
            low=Decimal("99.95"),
            open_=Decimal("100.00"),
            volume=1000,
        ))
        assert sig is None, "No entry signal when price is at VWAP"

    def test_no_entry_slightly_below_vwap_within_band(self):
        """Price slightly below VWAP but within deviation band should NOT trigger."""
        strat = make_strategy({"vwap_min_bars": 3, "entry_deviation_pct": "0.01"})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        # VWAP ~ 100.00, threshold ~ 99.00 (1%)
        # Price at 99.50 — below VWAP but above threshold
        sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=5),
            close=Decimal("99.50"),
            high=Decimal("99.55"),
            low=Decimal("99.45"),
            open_=Decimal("99.50"),
            volume=1000,
        ))
        assert sig is None, "No entry when price within deviation band"

    def test_no_entry_above_vwap(self):
        """Price above VWAP should never trigger entry."""
        strat = make_strategy({"vwap_min_bars": 3})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=1000,
            ))

        sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=5),
            close=Decimal("101.00"),
            high=Decimal("101.05"),
            low=Decimal("100.95"),
            open_=Decimal("101.00"),
            volume=1000,
        ))
        assert sig is None, "No entry when price above VWAP"


# ---------------------------------------------------------------------------
# Exit correctness
# ---------------------------------------------------------------------------

class TestExitCorrectness:
    """Exit signals fire under the correct conditions."""

    def _enter_position(self, strat, base_ts):
        """Helper: warm up and trigger entry, then simulate fill.

        Uses high volume on warmup bars so that the single low-price entry bar
        doesn't drag VWAP down enough to prevent triggering.
        """
        # Warm up at 100.00 with high volume to anchor VWAP
        for i in range(5):
            strat.on_bar(make_bar(
                timestamp=base_ts + timedelta(minutes=i),
                close=Decimal("100.00"),
                high=Decimal("100.05"),
                low=Decimal("99.95"),
                open_=Decimal("100.00"),
                volume=100000,
            ))

        # Entry bar well below VWAP (low volume so VWAP barely moves)
        # VWAP ~ 100.00, threshold (1% dev) ~ 99.00
        # close=97.00 is well below any reasonable threshold
        sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=6),
            close=Decimal("97.00"),
            high=Decimal("97.05"),
            low=Decimal("96.95"),
            open_=Decimal("97.00"),
            volume=100,
        ))
        assert sig is not None and sig.side == "BUY", (
            f"Expected BUY entry signal, got {sig}"
        )

        # Simulate fill
        strat.on_order_filled("ord-1", "SPY", sig.quantity, Decimal("97.00"))
        return sig

    def test_exit_on_mean_reversion_to_vwap(self):
        """When price reverts to VWAP, exit signal emitted."""
        strat = make_strategy({"vwap_min_bars": 5, "entry_deviation_pct": "0.01"})
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        self._enter_position(strat, base_ts)

        # Send bar at VWAP level — should trigger mean reversion exit
        exit_sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=8),
            close=Decimal("100.00"),
            high=Decimal("100.05"),
            low=Decimal("99.95"),
            open_=Decimal("100.00"),
            volume=1000,
        ))
        assert exit_sig is not None, "Expected exit on mean reversion"
        assert exit_sig.side == "SELL"
        assert "MEAN_REVERSION" in exit_sig.reason

    def test_exit_on_stop_loss(self):
        """When price hits stop loss, exit signal emitted."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "stop_loss_pct": "0.003",
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

        self._enter_position(strat, base_ts)

        # Entry was at 97.00, stop = 97.00 * (1 - 0.003) = 96.709
        exit_sig = strat.on_bar(make_bar(
            timestamp=base_ts + timedelta(minutes=8),
            close=Decimal("96.50"),  # below stop
            high=Decimal("96.55"),
            low=Decimal("96.45"),
            open_=Decimal("96.50"),
            volume=1000,
        ))
        assert exit_sig is not None, "Expected stop loss exit"
        assert exit_sig.side == "SELL"
        assert exit_sig.reason == "STOP_LOSS"

    def test_force_flat_eod(self):
        """Near close, position is force-flattened."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "flat_time": "15:55",
        })
        # Use a time within trade window for entry
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)  # 10:00 ET

        self._enter_position(strat, base_ts)

        # Now send bar at 15:55 ET = 20:55 UTC
        flat_ts = datetime(2026, 1, 30, 20, 55, 0, tzinfo=timezone.utc)
        exit_sig = strat.on_bar(make_bar(
            timestamp=flat_ts,
            close=Decimal("99.50"),
            high=Decimal("99.55"),
            low=Decimal("99.45"),
            open_=Decimal("99.50"),
            volume=1000,
        ))
        assert exit_sig is not None, "Expected FORCE_FLAT_EOD exit"
        assert exit_sig.side == "SELL"
        assert exit_sig.reason == "FORCE_FLAT_EOD"
