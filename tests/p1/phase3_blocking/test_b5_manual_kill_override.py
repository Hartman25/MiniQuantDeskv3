"""
P3-B5 — Manual Kill Override

INVARIANT:
    The IntradayDrawdownMonitor MUST support manual halt (force_halt)
    and manual reset (reset_halt) to allow human intervention.

    Manual reset after halt MUST transition to WARNING (not NORMAL)
    if drawdown is still above warning threshold.

TESTS:
    5 tests covering manual kill override mechanisms.
"""

from decimal import Decimal

from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus,
)


class TestManualKillOverride:

    def test_force_halt_overrides_normal(self):
        """force_halt() sets status to HALT regardless of drawdown."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        assert monitor.current_status == DrawdownStatus.NORMAL

        monitor.force_halt("Emergency: suspicious activity")
        assert monitor.current_status == DrawdownStatus.HALT
        assert monitor.is_trading_halted() is True

    def test_reset_halt_to_warning_when_drawdown_above_threshold(self):
        """reset_halt() sets WARNING (not NORMAL) when drawdown still high."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0"),
        )
        # Trigger halt
        monitor.update_equity(Decimal("8800"))  # 12% drawdown
        assert monitor.current_status == DrawdownStatus.HALT

        # Manual reset — still 12% down, should go to WARNING not NORMAL
        monitor.reset_halt()
        assert monitor.current_status == DrawdownStatus.WARNING

    def test_reset_halt_to_normal_when_drawdown_low(self):
        """reset_halt() sets NORMAL when drawdown below warning threshold."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0"),
        )
        # Force halt at current equity (no actual drawdown)
        monitor.force_halt("Test halt")
        assert monitor.current_status == DrawdownStatus.HALT

        # Reset — drawdown is 0%, should go to NORMAL
        monitor.reset_halt()
        assert monitor.current_status == DrawdownStatus.NORMAL

    def test_reset_daily_clears_halt(self):
        """reset_daily() clears halt and starts fresh."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        monitor.force_halt("EOD halt")
        assert monitor.is_trading_halted() is True

        monitor.reset_daily(Decimal("9500"))
        assert monitor.current_status == DrawdownStatus.NORMAL
        assert monitor.is_trading_halted() is False
        assert monitor.peak_equity == Decimal("9500")

    def test_halt_count_incremented(self):
        """force_halt increments halt_count statistic."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        assert monitor.halt_count == 0
        monitor.force_halt("Test 1")
        assert monitor.halt_count == 1
        monitor.force_halt("Test 2")
        assert monitor.halt_count == 2
