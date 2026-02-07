"""
PATCH 14 — Add deterministic research runner

INVARIANT:
    The ResearchRunner produces identical results for the same seed +
    bars + strategy.  Every decision is journal-logged.  No I/O occurs.

TESTS:
    1.  Empty run produces zero-bar report.
    2.  add_bar without strategy → NO_SIGNAL decision.
    3.  add_bar with strategy → SUBMIT_MARKET decision.
    4.  Fill uses close price by default.
    5.  Fill uses open price when configured.
    6.  Deterministic: same seed + bars → same report.
    7.  Different seed → different order IDs.
    8.  finalize() prevents further add_bar.
    9.  Double finalize raises.
   10.  Journal has start and end events.
   11.  ResearchReport.to_dict() is JSON-serializable.
   12.  config_hash is deterministic for same params.
   13.  reset() creates fresh runner with same config.
   14.  Clock advances per bar interval.
   15.  Skip decision when quantity is zero.
   16.  RNG is seeded and reproducible.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.research.runner import (
    ResearchDecision,
    ResearchFill,
    ResearchReport,
    ResearchRunner,
)


def _make_bar(symbol="SPY", close=100.0, open_=99.0, volume=1000):
    return {
        "symbol": symbol,
        "close": close,
        "open": open_,
        "high": close + 1,
        "low": close - 1,
        "volume": volume,
    }


def _buy_strategy(bar):
    """Always emits a BUY 10 shares MARKET order."""
    return {"side": "BUY", "quantity": 10, "order_type": "MARKET"}


class TestResearchRunner:

    def test_empty_run_zero_bars(self):
        """Empty run produces zero-bar report."""
        runner = ResearchRunner(seed=1)
        report = runner.finalize()
        assert report.bar_count == 0
        assert report.fill_count == 0
        assert report.skip_count == 0
        assert report.submit_count == 0

    def test_no_strategy_no_signal(self):
        """add_bar without strategy → NO_SIGNAL."""
        runner = ResearchRunner(seed=1)
        d = runner.add_bar(_make_bar())
        assert d.action == "NO_SIGNAL"
        assert d.symbol == "SPY"
        assert d.bar_index == 0

    def test_strategy_submit_market(self):
        """add_bar with strategy → SUBMIT_MARKET."""
        runner = ResearchRunner(seed=1, strategy_fn=_buy_strategy)
        d = runner.add_bar(_make_bar())
        assert d.action == "SUBMIT_MARKET"
        assert d.quantity == Decimal("10")
        assert d.side == "BUY"

    def test_fill_uses_close_price(self):
        """Fill price comes from 'close' by default."""
        runner = ResearchRunner(seed=1, strategy_fn=_buy_strategy)
        runner.add_bar(_make_bar(close=150.0))
        assert len(runner.fills) == 1
        assert runner.fills[0].price == Decimal("150.0")

    def test_fill_uses_open_price(self):
        """Fill price from 'open' when configured."""
        runner = ResearchRunner(
            seed=1, strategy_fn=_buy_strategy, fill_price_source="open"
        )
        runner.add_bar(_make_bar(close=150.0, open_=148.0))
        assert runner.fills[0].price == Decimal("148.0")

    def test_deterministic_same_seed(self):
        """Same seed + bars → identical reports."""
        bars = [_make_bar(close=100 + i) for i in range(5)]

        r1 = ResearchRunner(seed=42, strategy_fn=_buy_strategy)
        for b in bars:
            r1.add_bar(b)
        report1 = r1.finalize()

        r2 = ResearchRunner(seed=42, strategy_fn=_buy_strategy)
        for b in bars:
            r2.add_bar(b)
        report2 = r2.finalize()

        assert report1.config_hash == report2.config_hash
        assert report1.fill_count == report2.fill_count
        assert report1.bar_count == report2.bar_count
        for f1, f2 in zip(report1.fills, report2.fills):
            assert f1.order_id == f2.order_id
            assert f1.price == f2.price

    def test_different_seed_different_ids(self):
        """Different seeds → different order IDs."""
        bar = _make_bar()

        r1 = ResearchRunner(seed=1, strategy_fn=_buy_strategy)
        r1.add_bar(bar)

        r2 = ResearchRunner(seed=2, strategy_fn=_buy_strategy)
        r2.add_bar(bar)

        assert r1.fills[0].order_id != r2.fills[0].order_id

    def test_finalize_prevents_add_bar(self):
        """After finalize, add_bar raises."""
        runner = ResearchRunner(seed=1)
        runner.finalize()
        with pytest.raises(RuntimeError, match="finalized"):
            runner.add_bar(_make_bar())

    def test_double_finalize_raises(self):
        """Double finalize raises."""
        runner = ResearchRunner(seed=1)
        runner.finalize()
        with pytest.raises(RuntimeError, match="Already finalized"):
            runner.finalize()

    def test_journal_has_start_and_end(self):
        """Journal contains start and completed events."""
        runner = ResearchRunner(seed=1)
        runner.add_bar(_make_bar())
        report = runner.finalize()

        events = [e["event"] for e in report.journal]
        assert events[0] == "research_run_started"
        assert events[-1] == "research_run_completed"

    def test_report_to_dict_serializable(self):
        """ResearchReport.to_dict() is JSON-serializable."""
        runner = ResearchRunner(seed=1, strategy_fn=_buy_strategy)
        runner.add_bar(_make_bar())
        report = runner.finalize()

        d = report.to_dict()
        # Must not raise
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        assert "research_fill" not in d  # top level is summary
        assert d["seed"] == 1
        assert d["fill_count"] == 1

    def test_config_hash_deterministic(self):
        """Same params → same config hash."""
        r1 = ResearchRunner(seed=42)
        r2 = ResearchRunner(seed=42)
        assert r1.config_hash == r2.config_hash

        r3 = ResearchRunner(seed=99)
        assert r1.config_hash != r3.config_hash

    def test_reset_creates_fresh_runner(self):
        """reset() returns new runner with same config."""
        runner = ResearchRunner(seed=42, strategy_fn=_buy_strategy)
        runner.add_bar(_make_bar())
        report1 = runner.finalize()

        runner2 = runner.reset()
        runner2.add_bar(_make_bar())
        report2 = runner2.finalize()

        assert report1.config_hash == report2.config_hash
        assert report1.fill_count == report2.fill_count
        assert report1.fills[0].order_id == report2.fills[0].order_id

    def test_clock_advances_per_bar(self):
        """Clock advances by bar_interval per bar."""
        interval = timedelta(minutes=5)
        runner = ResearchRunner(seed=1, bar_interval=interval)

        t0 = runner.clock.now()
        runner.add_bar(_make_bar())
        t1 = runner.clock.now()
        runner.add_bar(_make_bar())
        t2 = runner.clock.now()

        assert t1 - t0 == interval
        assert t2 - t1 == interval

    def test_skip_when_qty_zero(self):
        """Strategy returning qty=0 → SKIP."""
        def zero_qty_strategy(bar):
            return {"side": "BUY", "quantity": 0, "order_type": "MARKET"}

        runner = ResearchRunner(seed=1, strategy_fn=zero_qty_strategy)
        d = runner.add_bar(_make_bar())
        assert d.action == "SKIP"
        assert d.reason == "qty_zero"
        assert len(runner.fills) == 0

    def test_rng_seeded_and_reproducible(self):
        """Seeded RNG produces same sequence."""
        r1 = ResearchRunner(seed=123)
        vals1 = [r1.rng.random() for _ in range(5)]

        r2 = ResearchRunner(seed=123)
        vals2 = [r2.rng.random() for _ in range(5)]

        assert vals1 == vals2
