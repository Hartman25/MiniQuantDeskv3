"""
P3-N2 — Idle-State Logging

INVARIANT:
    Protection objects MUST expose their status via get_status().
    When not triggered, is_protected MUST be False.
    When triggered, the status MUST include the protection name,
    seconds_remaining, and last_trigger type.

    The IntradayDrawdownMonitor MUST expose statistics for monitoring.

TESTS:
    4 tests covering idle-state status reporting and statistics.
"""

from datetime import timedelta
from decimal import Decimal

from core.risk.protections.stoploss_guard import StoplossGuard
from core.risk.protections.max_drawdown import MaxDrawdownProtection
from core.risk_management.drawdown import IntradayDrawdownMonitor, DrawdownStatus


class TestIdleStateLogging:

    def test_stoploss_guard_status_when_idle(self):
        """Idle StoplossGuard reports is_protected=False."""
        guard = StoplossGuard(max_stoplosses=3)
        status = guard.get_status()
        assert status['name'] == "StoplossGuard"
        assert status['enabled'] is True
        assert status['is_protected'] is False
        assert status['last_trigger'] is None

    def test_max_drawdown_status_when_idle(self):
        """Idle MaxDrawdownProtection reports is_protected=False."""
        prot = MaxDrawdownProtection(max_drawdown=0.15)
        status = prot.get_status()
        assert status['name'] == "MaxDrawdownProtection"
        assert status['enabled'] is True
        assert status['is_protected'] is False

    def test_drawdown_monitor_statistics(self):
        """IntradayDrawdownMonitor exposes session statistics."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        stats = monitor.get_statistics()
        assert "starting_equity" in stats
        assert "current_equity" in stats
        assert "current_status" in stats
        assert stats["current_status"] == "NORMAL"
        assert stats["warning_count"] == 0
        assert stats["halt_count"] == 0

    def test_drawdown_monitor_tracks_events(self):
        """IntradayDrawdownMonitor records events on status changes."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
        )
        # Trigger a warning
        monitor.update_equity(Decimal("9400"))  # 6% drawdown
        assert monitor.warning_count == 1
        events = monitor.get_recent_events()
        assert len(events) >= 1
