"""
P1-B3 — Invariant Violation → Halt

INVARIANT:
    When a safety invariant is violated, the runtime MUST halt (return
    exit code 1) rather than silently continuing.  Covered violations:

    1. Circuit breaker: N consecutive loop exceptions → halt
    2. Recovery failure: RecoveryStatus.FAILED → halt (before loop)
    3. Live-mode reconciliation discrepancy → halt (before loop)
    4. Any single exception in run_once mode → halt
    5. SubsystemHealthMonitor: critical subsystem failure → should_halt()

HALT DECISION:
    All invariant violations HALT (return 1).  The only exception is
    fail-open in _single_trade_should_block_entry and _load_protective_stops,
    which are explicitly documented as fail-open because blocking a trade
    is safer than crashing the whole system.

TESTS:
    These tests validate the contract at multiple layers:
    - Circuit breaker unit behavior (already covered in patch1)
    - Runtime integration: exception → return 1 in run_once
    - Runtime integration: circuit breaker trip → return 1
    - Subsystem health monitor: critical failure → should_halt()
    - Recovery FAILED → return 1 (already covered in patch1/patch7)

    We add only the MISSING tests here:
    - Explicit test that run_once + exception = exit 1
    - Subsystem health monitor integration
    - Circuit breaker counts are correct after mixed success/failure
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd


# ===================================================================
# 1. Runtime: run_once + exception → exit code 1
# ===================================================================

class TestRunOnceExceptionHalts:

    def test_run_once_exception_returns_1(self, monkeypatch, tmp_path):
        """In run_once mode, any exception in the main loop must exit 1."""
        import core.runtime.app as app_mod
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[])
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _ExplodingBroker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self):
                raise RuntimeError("broker is on fire")
            def get_bars(self, *a, **kw): return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []
            def get_positions(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _ExplodingBroker)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 1, "run_once with exception must return exit code 1"


# ===================================================================
# 2. Circuit breaker: mixed success/failure tracking
# ===================================================================

class TestCircuitBreakerInvariant:

    def test_breaker_resets_count_on_success(self):
        """A success between failures must reset the counter."""
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        assert not cb.is_tripped

        cb.record_success()
        assert cb.failure_count == 0

        cb.record_failure()
        assert cb.failure_count == 1
        assert not cb.is_tripped

    def test_breaker_trips_at_exact_threshold(self):
        """Breaker must trip when count reaches max_failures exactly."""
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
        cb = ConsecutiveFailureBreaker(max_failures=3)

        cb.record_failure()
        cb.record_failure()
        assert not cb.is_tripped

        cb.record_failure()
        assert cb.is_tripped
        assert cb.failure_count == 3

    def test_breaker_threshold_configurable(self):
        """Threshold must be configurable."""
        from core.runtime.circuit_breaker import ConsecutiveFailureBreaker

        cb1 = ConsecutiveFailureBreaker(max_failures=1)
        cb1.record_failure()
        assert cb1.is_tripped

        cb10 = ConsecutiveFailureBreaker(max_failures=10)
        for _ in range(9):
            cb10.record_failure()
        assert not cb10.is_tripped
        cb10.record_failure()
        assert cb10.is_tripped


# ===================================================================
# 3. Subsystem health monitor
# ===================================================================

class TestSubsystemHealthMonitor:

    def test_critical_subsystem_failure_triggers_halt(self):
        """When a critical subsystem exceeds failure threshold, should_halt."""
        from core.runtime.subsystem_health import SubsystemHealthMonitor

        mon = SubsystemHealthMonitor(
            critical_subsystems={"broker", "data_feed"},
            failure_threshold=3,
        )

        mon.record_failure("broker")
        mon.record_failure("broker")
        assert not mon.should_halt()

        mon.record_failure("broker")
        assert mon.should_halt(), "3 consecutive broker failures should trigger halt"

    def test_non_critical_subsystem_does_not_halt(self):
        """Non-critical subsystem failures should NOT trigger halt."""
        from core.runtime.subsystem_health import SubsystemHealthMonitor

        mon = SubsystemHealthMonitor(
            critical_subsystems={"broker"},
            failure_threshold=2,
        )

        mon.record_failure("logging")
        mon.record_failure("logging")
        mon.record_failure("logging")
        assert not mon.should_halt()

    def test_success_resets_failure_count(self):
        """record_ok resets the consecutive failure counter for that subsystem."""
        from core.runtime.subsystem_health import SubsystemHealthMonitor

        mon = SubsystemHealthMonitor(
            critical_subsystems={"broker"},
            failure_threshold=3,
        )

        mon.record_failure("broker")
        mon.record_failure("broker")
        mon.record_ok("broker")

        mon.record_failure("broker")
        assert not mon.should_halt(), "reset should prevent halt at 1 failure"

    def test_get_status_reports_counts(self):
        """get_status must report per-subsystem failure counts."""
        from core.runtime.subsystem_health import SubsystemHealthMonitor

        mon = SubsystemHealthMonitor(
            critical_subsystems={"broker"},
            failure_threshold=5,
        )

        mon.record_failure("broker")
        mon.record_failure("broker")
        mon.record_failure("data")

        status = mon.get_status()
        assert status["broker"]["consecutive_failures"] == 2
        assert status["data"]["consecutive_failures"] == 1


# ===================================================================
# 4. Recovery FAILED → halt (strict, no conditional guards)
# ===================================================================

class TestRecoveryFailedHalts:

    def test_recovery_failed_returns_exit_1(self, monkeypatch, tmp_path):
        """If _try_recovery returns FAILED, run() must return 1."""
        import core.runtime.app as app_mod
        from core.recovery.coordinator import RecoveryStatus
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[])
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _Broker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self):
                return {"portfolio_value": "100000", "buying_power": "50000"}
            def get_bars(self, *a, **kw): return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []
            def get_positions(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _Broker)
        monkeypatch.setattr(
            app_mod, "_try_recovery",
            lambda *a, **kw: RecoveryStatus.FAILED,
        )

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 1
