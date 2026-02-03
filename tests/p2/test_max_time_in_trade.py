"""
Phase 2 — Max Time-in-Trade Tests

Invariants covered:
  P2-INV-09: Position held longer than max_time_in_trade_minutes triggers TIMEOUT exit
  P2-INV-10: TIMEOUT exit reason is "MAX_TIME_IN_TRADE"

Uses bar.timestamp for elapsed time — never wall clock.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from tests.p2.conftest import make_bar, make_strategy


def _enter_position(strat, base_ts):
    """Warm up, trigger entry, simulate fill. Returns entry signal."""
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

    # Entry bar well below VWAP
    sig = strat.on_bar(make_bar(
        timestamp=base_ts + timedelta(minutes=6),
        close=Decimal("97.00"),
        high=Decimal("97.05"),
        low=Decimal("96.95"),
        open_=Decimal("97.00"),
        volume=100,
    ))
    assert sig is not None and sig.side == "BUY"
    strat.on_order_filled("ord-1", "SPY", sig.quantity, Decimal("97.00"))
    return sig, base_ts + timedelta(minutes=6)


class TestMaxTimeInTrade:
    """P2-INV-09 / P2-INV-10: Time-based exit with TIMEOUT reason."""

    def test_timeout_triggers_when_over_limit(self):
        """Position older than max_time_in_trade_minutes => TIMEOUT exit."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "max_time_in_trade_minutes": 10,
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        _, entry_ts = _enter_position(strat, base_ts)

        # Bar 11 minutes after entry => over limit
        exit_bar_ts = entry_ts + timedelta(minutes=11)
        sig = strat.on_bar(make_bar(
            timestamp=exit_bar_ts,
            close=Decimal("98.00"),  # between entry and VWAP — no other exit trigger
            high=Decimal("98.05"),
            low=Decimal("97.95"),
            open_=Decimal("98.00"),
            volume=1000,
        ))
        assert sig is not None, "Expected TIMEOUT exit signal"
        assert sig.side == "SELL"
        assert sig.reason == "MAX_TIME_IN_TRADE"

    def test_no_timeout_when_under_limit(self):
        """Position younger than max_time_in_trade_minutes => no TIMEOUT."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "max_time_in_trade_minutes": 30,
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        _, entry_ts = _enter_position(strat, base_ts)

        # Bar 5 minutes after entry => under limit
        bar_ts = entry_ts + timedelta(minutes=5)
        sig = strat.on_bar(make_bar(
            timestamp=bar_ts,
            close=Decimal("98.00"),
            high=Decimal("98.05"),
            low=Decimal("97.95"),
            open_=Decimal("98.00"),
            volume=1000,
        ))
        # Should be None (no exit trigger: not at VWAP, not at stop, not timed out)
        assert sig is None, f"No exit expected under time limit, got {sig}"

    def test_timeout_disabled_when_zero(self):
        """max_time_in_trade_minutes=0 means timeout is disabled."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "max_time_in_trade_minutes": 0,
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        _, entry_ts = _enter_position(strat, base_ts)

        # Bar 60 minutes later — should NOT timeout
        bar_ts = entry_ts + timedelta(minutes=60)
        sig = strat.on_bar(make_bar(
            timestamp=bar_ts,
            close=Decimal("98.00"),
            high=Decimal("98.05"),
            low=Decimal("97.95"),
            open_=Decimal("98.00"),
            volume=1000,
        ))
        assert sig is None, "Timeout disabled (0) should not trigger exit"

    def test_timeout_exactly_at_limit(self):
        """Position at exactly max_time_in_trade_minutes => TIMEOUT."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "max_time_in_trade_minutes": 10,
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        _, entry_ts = _enter_position(strat, base_ts)

        # Bar exactly 10 minutes after entry
        bar_ts = entry_ts + timedelta(minutes=10)
        sig = strat.on_bar(make_bar(
            timestamp=bar_ts,
            close=Decimal("98.00"),
            high=Decimal("98.05"),
            low=Decimal("97.95"),
            open_=Decimal("98.00"),
            volume=1000,
        ))
        assert sig is not None, "Expected TIMEOUT at exact limit"
        assert sig.reason == "MAX_TIME_IN_TRADE"

    def test_force_flat_eod_takes_priority_over_timeout(self):
        """Force-flat-EOD should trigger before timeout check."""
        strat = make_strategy({
            "vwap_min_bars": 5,
            "entry_deviation_pct": "0.01",
            "max_time_in_trade_minutes": 10,
            "flat_time": "15:55",
        })
        base_ts = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)
        _, entry_ts = _enter_position(strat, base_ts)

        # Bar at 15:55 ET (20:55 UTC), 60+ minutes after entry
        flat_ts = datetime(2026, 1, 30, 20, 55, 0, tzinfo=timezone.utc)
        sig = strat.on_bar(make_bar(
            timestamp=flat_ts,
            close=Decimal("98.00"),
            high=Decimal("98.05"),
            low=Decimal("97.95"),
            open_=Decimal("98.00"),
            volume=1000,
        ))
        assert sig is not None
        assert sig.reason == "FORCE_FLAT_EOD", (
            f"Force-flat-EOD should take priority, got {sig.reason}"
        )
