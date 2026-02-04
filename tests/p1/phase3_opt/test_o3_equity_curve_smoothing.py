"""
P3-O3 — Equity-Curve Smoothing Rules

INVARIANT:
    The IntradayDrawdownMonitor MUST:
    - Reset peak on new equity highs (reset_on_new_high=True)
    - Track max_drawdown_today for session analysis
    - Support daily reset with new starting equity

    These mechanisms smooth the equity curve by preventing trading
    during drawdowns and allowing recovery before resuming.

TESTS:
    4 tests proving equity-curve smoothing mechanisms work.
"""

from decimal import Decimal

from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus,
)


class TestEquityCurveSmoothing:

    def test_new_high_resets_peak(self):
        """New equity high resets peak_equity."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            reset_on_new_high=True,
        )
        monitor.update_equity(Decimal("10500"))
        assert monitor.peak_equity == Decimal("10500")

    def test_max_drawdown_today_tracked(self):
        """max_drawdown_today tracks worst drawdown in session."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        monitor.update_equity(Decimal("9500"))  # 5% drawdown
        monitor.update_equity(Decimal("9800"))  # recovered to 2%
        assert monitor.get_max_drawdown_today() >= Decimal("5.0")

    def test_daily_reset_clears_statistics(self):
        """reset_daily() clears all stats and sets new starting equity."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        monitor.update_equity(Decimal("9000"))  # 10% drawdown
        monitor.reset_daily(Decimal("9000"))
        assert monitor.starting_equity == Decimal("9000")
        assert monitor.peak_equity == Decimal("9000")
        assert monitor.max_drawdown_today == Decimal("0")
        assert monitor.current_status == DrawdownStatus.NORMAL

    def test_recovery_after_warning(self):
        """Recovering above warning threshold → NORMAL."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0"),
        )
        # Drop to warning
        monitor.update_equity(Decimal("9400"))
        assert monitor.current_status == DrawdownStatus.WARNING
        # Recover — new high resets peak, 0% drawdown
        monitor.update_equity(Decimal("10100"))
        assert monitor.current_status == DrawdownStatus.NORMAL
