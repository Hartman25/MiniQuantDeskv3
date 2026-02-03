"""
P3-O1 — Dynamic Size Throttling After Drawdowns

INVARIANT:
    The IntradayDrawdownMonitor MUST provide drawdown percentage that
    can be used to reduce position sizes during drawdowns.

    Position sizing MUST be reducible based on drawdown state:
    at WARNING → should trade smaller or halt.

TESTS:
    3 tests proving drawdown-based throttling is possible.
"""

from decimal import Decimal

from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus,
)


class TestDynamicSizeThrottling:

    def test_drawdown_percent_available(self):
        """get_drawdown_percent() returns current drawdown %."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
        )
        monitor.update_equity(Decimal("9700"))  # 3% drawdown
        dd = monitor.get_drawdown_percent()
        assert dd > Decimal("0")
        assert dd <= Decimal("100")

    def test_normal_status_allows_full_size(self):
        """NORMAL status → no throttling needed."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
        )
        monitor.update_equity(Decimal("9800"))  # 2% drawdown
        assert monitor.get_status() == DrawdownStatus.NORMAL

    def test_warning_status_indicates_throttle(self):
        """WARNING status → should reduce size."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0"),
        )
        monitor.update_equity(Decimal("9300"))  # 7% drawdown
        assert monitor.get_status() == DrawdownStatus.WARNING
        # Drawdown percent can be used to calculate size reduction
        dd = monitor.get_drawdown_percent()
        assert dd >= Decimal("5.0")
