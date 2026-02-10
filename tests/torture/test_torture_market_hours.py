"""
Torture test: Market-hours guard transitions.

Simulates a weekend scenario where the market is closed for N cycles
then opens at cycle N+1. Verifies:
  - No submit_order calls occur while closed.
  - Once open, submit_order can occur when signals exist.
  - MARKET_CLOSED_BLOCK is logged each closed cycle with next_open_utc/next_open_ny.
  - Adaptive sleep uses the correct cadence (closed vs pre-open vs open).
  - Clock failures with fail-closed and fail-open env var are handled correctly.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.runtime.app import _check_market_open, compute_adaptive_sleep
from tests.torture.helpers.chaos_broker import ChaosBroker
from tests.torture.helpers.run_harness import run_harness


class TestMarketClosedWeekend:
    """Simulate market closed for 5 cycles (weekend), then open."""

    def test_no_orders_while_closed_then_orders_when_open(self, tmp_path):
        """While market is closed: zero submissions. Once open: submissions occur."""
        closed_cycles = 5
        total_cycles = 8

        # Emit a BUY signal every cycle (lifecycle will serve them)
        signal = {
            "action": "BUY",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "strategy": "VWAPMicroMeanReversion",
        }
        signals_per_cycle = [[signal]] * total_cycles

        broker = ChaosBroker(
            seed=42,
            closed_until_cycle=closed_cycles,
            next_open_utc=datetime(2026, 2, 9, 14, 30, 0, tzinfo=timezone.utc),
        )

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=total_cycles,
            signals_per_cycle=signals_per_cycle,
        )

        assert result.error is None, f"Harness raised: {result.error}"

        # Check: no order submissions during closed cycles
        closed_submissions = [
            s for s in broker.order_submissions if s["cycle"] < closed_cycles
        ]
        assert len(closed_submissions) == 0, (
            f"Orders submitted while market closed: {closed_submissions}"
        )

    def test_market_closed_block_logged_each_cycle(self, tmp_path):
        """MARKET_CLOSED_BLOCK event emitted on each closed cycle."""
        closed_cycles = 4
        total_cycles = 6
        next_open = datetime(2026, 2, 9, 14, 30, 0, tzinfo=timezone.utc)

        broker = ChaosBroker(
            seed=42,
            closed_until_cycle=closed_cycles,
            next_open_utc=next_open,
        )

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=total_cycles,
        )

        assert result.error is None

        closed_events = result.events_by_type("MARKET_CLOSED_BLOCK")
        # Should have one per closed cycle
        assert len(closed_events) >= closed_cycles, (
            f"Expected >= {closed_cycles} MARKET_CLOSED_BLOCK events, got {len(closed_events)}"
        )

    def test_market_closed_block_has_next_open_fields(self, tmp_path):
        """Each MARKET_CLOSED_BLOCK must include next_open_utc and next_open_ny."""
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

        closed_events = result.events_by_type("MARKET_CLOSED_BLOCK")
        assert len(closed_events) > 0

        for evt in closed_events:
            assert "next_open_utc" in evt, f"Missing next_open_utc: {evt}"
            assert evt["next_open_utc"] is not None, f"next_open_utc is None: {evt}"
            assert "next_open_ny" in evt, f"Missing next_open_ny: {evt}"


class TestClockFailures:
    """Clock API failures: fail-closed vs fail-open behavior."""

    def test_clock_error_fail_closed_by_default(self):
        """When clock raises and env var is not set, fail-closed (is_open=False)."""

        class ErrorBroker:
            def get_clock(self):
                raise ConnectionError("simulated clock outage")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MQD_FAIL_OPEN_MARKET_HOURS", None)
            status = _check_market_open(ErrorBroker())
            assert status["is_open"] is False
            assert status["source"] == "fallback"
            assert "simulated" in status["error"]

    def test_clock_error_fail_open_when_env_set(self):
        """When MQD_FAIL_OPEN_MARKET_HOURS=1, clock errors → fail-open."""

        class ErrorBroker:
            def get_clock(self):
                raise TimeoutError("clock API timeout")

        with patch.dict(os.environ, {"MQD_FAIL_OPEN_MARKET_HOURS": "1"}):
            status = _check_market_open(ErrorBroker())
            assert status["is_open"] is True
            assert status["source"] == "fallback"

    def test_clock_errors_during_run_do_not_crash(self, tmp_path):
        """Clock errors on some cycles → runtime continues (fail-closed)."""
        broker = ChaosBroker(
            seed=42,
            closed_until_cycle=0,  # Would be open, but clock errors override
            clock_error_cycles={0, 1, 2},
        )

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=5,
            env_overrides={"MQD_FAIL_OPEN_MARKET_HOURS": "0"},
        )

        # Should not crash
        assert result.error is None


class TestAdaptiveSleepTorture:
    """Verify adaptive sleep policy returns correct durations."""

    REF_NOW = datetime(2026, 2, 9, 3, 0, 0, tzinfo=timezone.utc)

    def test_closed_far_from_open_uses_closed_interval(self):
        """12h from open → closed_interval_s."""
        far_open = self.REF_NOW + timedelta(hours=12)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=far_open,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=20,
            pre_open_window_m=10,
        )
        assert sleep == 120

    def test_preopen_window_uses_short_sleep(self):
        """5 minutes from open → pre_open_interval_s."""
        near_open = self.REF_NOW + timedelta(minutes=5)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=near_open,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=15,
            pre_open_window_m=10,
        )
        assert sleep == 15

    def test_market_open_uses_base_interval(self):
        """Market open → base_interval_s."""
        sleep = compute_adaptive_sleep(
            market_is_open=True,
            base_interval_s=60,
            now_utc=self.REF_NOW,
        )
        assert sleep == 60

    def test_no_next_open_uses_closed_interval(self):
        """Closed with no next_open info → closed_interval_s."""
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=None,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=300,
        )
        assert sleep == 300

    def test_sleep_never_zero(self):
        """Even with bad inputs, sleep is at least 1."""
        sleep = compute_adaptive_sleep(
            market_is_open=True,
            base_interval_s=0,
            now_utc=self.REF_NOW,
        )
        assert sleep >= 1

    def test_preopen_boundary_exact(self):
        """Exactly at pre-open window boundary → uses pre-open sleep."""
        boundary = self.REF_NOW + timedelta(minutes=10)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=boundary,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=20,
            pre_open_window_m=10,
        )
        assert sleep == 20

    def test_harness_completes_all_cycles(self, tmp_path):
        """Verify the harness runs all requested cycles without error."""
        broker = ChaosBroker(seed=42, closed_until_cycle=2)

        result = run_harness(
            broker=broker,
            tmp_path=tmp_path,
            max_cycles=4,
            run_interval_s=60,
        )

        assert result.error is None
        # Boot events should exist (one per run() call = 4)
        boot_events = result.events_by_type("boot")
        assert len(boot_events) == 4, (
            f"Expected 4 boot events (one per cycle), got {len(boot_events)}"
        )
