"""
P3-B4 — Automated Kill Switches

INVARIANT:
    The system MUST have automated kill switches that halt all trading
    when critical thresholds are breached:
      - KillSwitchActivatedEvent records reason + trigger_source
      - IntradayDrawdownMonitor transitions to HALT on excessive drawdown
      - EventHandlerRegistry logs kill switch events to transaction_log

TESTS:
    5 tests covering automated kill switch mechanisms.
"""

from datetime import datetime, timezone
from decimal import Decimal

from core.events.types import KillSwitchActivatedEvent
from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus,
)


class TestKillSwitchEvent:

    def test_event_has_required_fields(self):
        """KillSwitchActivatedEvent has reason, trigger_source, and status flags."""
        event = KillSwitchActivatedEvent(
            reason="Daily loss limit exceeded",
            trigger_source="DailyLossLimitProtection",
            all_positions_closed=True,
            all_orders_cancelled=True,
        )
        assert event.reason == "Daily loss limit exceeded"
        assert event.trigger_source == "DailyLossLimitProtection"
        assert event.all_positions_closed is True
        assert event.all_orders_cancelled is True

    def test_event_is_frozen(self):
        """KillSwitchActivatedEvent is immutable (frozen dataclass)."""
        event = KillSwitchActivatedEvent(
            reason="test", trigger_source="test",
            all_positions_closed=False, all_orders_cancelled=False,
        )
        try:
            event.reason = "modified"
            assert False, "Should not be able to modify frozen dataclass"
        except AttributeError:
            pass  # Expected

    def test_event_has_timestamp(self):
        """Event has auto-generated timestamp."""
        event = KillSwitchActivatedEvent(
            reason="test", trigger_source="test",
            all_positions_closed=False, all_orders_cancelled=False,
        )
        assert event.timestamp is not None
        assert event.timestamp.tzinfo is not None


class TestIntradayDrawdownKillSwitch:

    def test_halt_on_excessive_drawdown(self):
        """Drawdown beyond halt threshold → HALT status."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            halt_threshold_percent=Decimal("10.0"),
        )
        # Drop 10% from peak
        status = monitor.update_equity(Decimal("9000"))
        assert status == DrawdownStatus.HALT
        assert monitor.is_trading_halted() is True

    def test_warning_before_halt(self):
        """Drawdown at warning threshold → WARNING, not HALT."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0"),
        )
        # Drop 6% from peak — between warning and halt
        status = monitor.update_equity(Decimal("9400"))
        assert status == DrawdownStatus.WARNING
        assert monitor.is_trading_halted() is False
