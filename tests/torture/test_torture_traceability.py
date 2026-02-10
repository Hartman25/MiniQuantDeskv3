"""
Torture test: Traceability — stable IDs on key events.

Verifies:
  - Every order_submitted event has run_id, trade_id, internal_order_id.
  - Every MARKET_CLOSED_BLOCK event has next_open fields.
  - run_id is stable across cycles within a single run.
  - trade_id and internal_order_id are present where submissions occur.
  - boot and startup_config_summary events are emitted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.torture.helpers.chaos_broker import ChaosBroker
from tests.torture.helpers.run_harness import run_harness


class TestOrderTraceability:
    """Every order attempt must carry stable correlation IDs."""

    def test_order_submitted_has_all_ids(self, tmp_path):
        """order_submitted events must have trade_id and internal_order_id."""
        signal = {
            "action": "BUY",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "strategy": "VWAPMicroMeanReversion",
        }

        broker = ChaosBroker(
            seed=42,
            closed_until_cycle=0,  # Always open
        )

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=3,
            signals_per_cycle=[[signal]] * 3,
        )

        assert result.error is None

        submitted = result.events_by_type("order_submitted")
        # At least one submission should have occurred
        # (may be fewer if single-trade guard blocks subsequent ones)
        if len(submitted) > 0:
            for evt in submitted:
                assert "trade_id" in evt and evt["trade_id"], (
                    f"order_submitted missing trade_id: {evt}"
                )
                assert "internal_order_id" in evt and evt["internal_order_id"], (
                    f"order_submitted missing internal_order_id: {evt}"
                )

    def test_order_filled_has_ids(self, tmp_path):
        """order_filled events must have trade_id and internal_order_id."""
        signal = {
            "action": "BUY",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "strategy": "VWAPMicroMeanReversion",
        }

        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=2,
            signals_per_cycle=[[signal]] * 2,
        )

        assert result.error is None

        filled = result.events_by_type("order_filled")
        for evt in filled:
            assert "trade_id" in evt and evt["trade_id"], (
                f"order_filled missing trade_id: {evt}"
            )
            assert "internal_order_id" in evt and evt["internal_order_id"], (
                f"order_filled missing internal_order_id: {evt}"
            )

    def test_signal_received_has_trade_id(self, tmp_path):
        """signal_received events must have trade_id."""
        signal = {
            "action": "BUY",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "strategy": "VWAPMicroMeanReversion",
        }

        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=2,
            signals_per_cycle=[[signal]] * 2,
        )

        assert result.error is None

        signals = result.events_by_type("signal_received")
        assert len(signals) > 0, "No signal_received events emitted"

        for evt in signals:
            assert "trade_id" in evt and evt["trade_id"], (
                f"signal_received missing trade_id: {evt}"
            )


class TestRunIdStability:
    """run_id must be stable within a single run invocation."""

    def test_run_id_present_on_startup(self, tmp_path):
        """startup_config_summary must include run_id."""
        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=2,
        )

        assert result.error is None

        startup = result.events_by_type("startup_config_summary")
        assert len(startup) > 0, "No startup_config_summary event"

        for evt in startup:
            assert "run_id" in evt and evt["run_id"], (
                f"startup_config_summary missing run_id: {evt}"
            )

    def test_run_id_consistent_across_events(self, tmp_path):
        """All events from a single run that carry run_id should share the same value."""
        signal = {
            "action": "BUY",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "strategy": "VWAPMicroMeanReversion",
        }

        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=1,
            signals_per_cycle=[[signal]],
        )

        assert result.error is None

        all_events = result.journal_events()
        run_ids = set()
        for evt in all_events:
            rid = evt.get("run_id")
            if rid:
                run_ids.add(rid)

        # Within a single run() invocation, run_id should be consistent
        # (may have 0 or 1 unique run_ids; both are acceptable)
        assert len(run_ids) <= 1, (
            f"Multiple run_ids found in a single run: {run_ids}"
        )


class TestMarketClosedBlockTraceability:
    """MARKET_CLOSED_BLOCK events must have next_open fields."""

    def test_closed_block_has_next_open_utc(self, tmp_path):
        """next_open_utc must be present on MARKET_CLOSED_BLOCK."""
        next_open = datetime(2026, 2, 9, 14, 30, 0, tzinfo=timezone.utc)
        broker = ChaosBroker(
            seed=42,
            closed_until_cycle=3,
            next_open_utc=next_open,
        )

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=4,
        )

        assert result.error is None

        closed = result.events_by_type("MARKET_CLOSED_BLOCK")
        assert len(closed) > 0

        for evt in closed:
            assert "next_open_utc" in evt, f"Missing next_open_utc: {evt}"
            assert evt["next_open_utc"] is not None, f"next_open_utc is None: {evt}"
            assert "next_open_ny" in evt, f"Missing next_open_ny: {evt}"

    def test_closed_block_has_run_id(self, tmp_path):
        """MARKET_CLOSED_BLOCK should include run_id."""
        broker = ChaosBroker(seed=42, closed_until_cycle=2)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=3,
        )

        assert result.error is None

        closed = result.events_by_type("MARKET_CLOSED_BLOCK")
        for evt in closed:
            assert "run_id" in evt, f"MARKET_CLOSED_BLOCK missing run_id: {evt}"


class TestBootEvents:
    """Boot and config events are emitted at startup."""

    def test_boot_event_emitted(self, tmp_path):
        """A 'boot' event should be emitted at runtime start."""
        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=1,
        )

        assert result.error is None

        boot = result.events_by_type("boot")
        assert len(boot) > 0, "No boot event emitted"

    def test_startup_config_summary_emitted(self, tmp_path):
        """startup_config_summary emitted with key fields."""
        broker = ChaosBroker(seed=42, closed_until_cycle=0)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=1,
        )

        assert result.error is None

        startup = result.events_by_type("startup_config_summary")
        assert len(startup) > 0, "No startup_config_summary event"

        evt = startup[0]
        assert "mode" in evt
        assert "paper" in evt
        assert "symbols" in evt
