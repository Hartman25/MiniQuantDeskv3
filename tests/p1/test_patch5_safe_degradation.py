"""
P1 Patch 5 – Safe Degradation on Subsystem Failure

INVARIANT:
    If a critical subsystem (journal, data feed) fails persistently
    within otherwise-successful cycles, the system must halt trading
    rather than continuing without audit trail or valid data.

WHY THIS MATTERS:
    The journal writer wraps all errors in try/except and continues.
    If the disk is full or the journal file is locked, the system
    trades without any audit log — violating compliance and making
    crash recovery impossible.

DESIGN:
    - New class `SubsystemHealthMonitor` tracks per-subsystem failures.
    - `record_ok(name)` / `record_failure(name)` called from runtime.
    - `should_halt()` returns True if any critical subsystem exceeds
      its failure threshold.
"""

import pytest


class TestSubsystemHealthMonitor:
    """Unit tests for the health monitor."""

    def test_initial_state_is_healthy(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor()
        assert mon.should_halt() is False

    def test_non_critical_failures_do_not_halt(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor(critical_subsystems={"journal"})
        for _ in range(10):
            mon.record_failure("discord_notifier")
        assert mon.should_halt() is False

    def test_critical_subsystem_trips_after_threshold(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor(
            critical_subsystems={"journal"},
            failure_threshold=3,
        )
        mon.record_failure("journal")
        mon.record_failure("journal")
        assert mon.should_halt() is False
        mon.record_failure("journal")
        assert mon.should_halt() is True

    def test_success_resets_failure_count(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor(
            critical_subsystems={"journal"},
            failure_threshold=3,
        )
        mon.record_failure("journal")
        mon.record_failure("journal")
        mon.record_ok("journal")  # resets
        mon.record_failure("journal")
        mon.record_failure("journal")
        assert mon.should_halt() is False

    def test_multiple_critical_subsystems(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor(
            critical_subsystems={"journal", "data_feed"},
            failure_threshold=2,
        )
        mon.record_failure("data_feed")
        mon.record_failure("data_feed")
        assert mon.should_halt() is True

    def test_get_status_report(self):
        from core.runtime.subsystem_health import SubsystemHealthMonitor
        mon = SubsystemHealthMonitor(
            critical_subsystems={"journal"},
            failure_threshold=3,
        )
        mon.record_failure("journal")
        mon.record_ok("data_feed")
        report = mon.get_status()
        assert "journal" in report
        assert report["journal"]["consecutive_failures"] == 1
