"""
P1 Patch 1 – Crash-Loop Circuit Breaker

INVARIANT:
    If the runtime main loop encounters MAX_CONSECUTIVE_FAILURES consecutive
    unhandled exceptions, it MUST halt (return exit-code 1) rather than
    retrying forever.

WHY THIS MATTERS:
    Without a circuit breaker the loop retries on every cycle when a
    persistent fault occurs (broker API down, network outage, corrupt
    config).  This causes:
      - Order storms / duplicate submissions on transient recovery
      - API rate-limit bans
      - Unbounded log growth
      - No human-visible signal that something is wrong

DESIGN:
    - New module `core/runtime/circuit_breaker.py` with a tiny
      ConsecutiveFailureBreaker class.
    - `app.py` increments on exception, resets on successful cycle,
      and halts when the threshold is reached.
    - Threshold is configurable via MAX_CONSECUTIVE_FAILURES env var
      (default 5).
"""

import pytest


# ---------------------------------------------------------------------------
# Unit tests for the breaker itself
# ---------------------------------------------------------------------------

class TestConsecutiveFailureBreaker:
    """Pure unit tests – no I/O, no runtime."""

    def test_initial_state_is_closed(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)
        assert cb.is_tripped is False
        assert cb.failure_count == 0

    def test_trips_after_max_failures(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_tripped is False
        cb.record_failure()
        assert cb.is_tripped is True
        assert cb.failure_count == 3

    def test_reset_clears_counter(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.is_tripped is False

    def test_success_between_failures_prevents_trip(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()   # resets
        cb.record_failure()
        cb.record_failure()
        assert cb.is_tripped is False

    def test_threshold_one(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=1)
        cb.record_failure()
        assert cb.is_tripped is True

    def test_default_threshold_is_five(self):
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker()
        assert cb.max_failures == 5


# ---------------------------------------------------------------------------
# Integration: runtime loop must halt after N consecutive failures
# ---------------------------------------------------------------------------

class TestRuntimeCircuitBreakerIntegration:
    """
    Verify that the runtime loop in app.py uses the circuit breaker
    and returns exit code 1 when the threshold is reached.
    """

    def test_runtime_halts_after_consecutive_failures(self, monkeypatch, tmp_path):
        """
        Simulate a broker that always raises.  The loop should stop
        after MAX_CONSECUTIVE_FAILURES iterations and return 1.
        """
        import core.runtime.app as app_mod
        from tests.conftest import (
            FakeConfig, FakeContainer, _fake_df_to_contracts,
        )
        import pandas as pd

        # Force the threshold to 3 for a fast test
        monkeypatch.setenv("MAX_CONSECUTIVE_FAILURES", "3")

        signals = []  # no signals needed – the error is in get_account_info
        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=signals)

        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        # Broker stub that always explodes
        class _ExplodingBroker:
            def __init__(self, *a, **kw):
                pass
            def get_account_info(self):
                raise ConnectionError("broker is down")
            def get_bars(self, *a, **kw):
                raise ConnectionError("broker is down")
            def get_orders(self):
                return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _ExplodingBroker)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper",
            run_interval_s=0,   # no sleep – fast test
            run_once=False,     # must NOT be run_once; we need the loop
        )

        rc = app_mod.run(opts)
        assert rc == 1, "Runtime must halt with exit-code 1 after consecutive failures"

    def test_runtime_resets_breaker_on_success(self):
        """
        If a success occurs between failures the counter must reset.
        We test this at the unit level on the breaker itself since the
        runtime integration for "halt" is covered by the test above.
        A reset means 2 failures + 1 success + 2 failures != 4 consecutive.
        """
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)
        cb.record_failure()  # 1
        cb.record_failure()  # 2
        cb.record_success()  # reset -> 0
        cb.record_failure()  # 1
        cb.record_failure()  # 2
        assert cb.is_tripped is False, "Counter should have reset on success"
        cb.record_failure()  # 3
        assert cb.is_tripped is True, "Now it should trip"
