"""
P3-B3 — Loss Clustering Detection

INVARIANT:
    The StoplossGuard MUST detect consecutive losses within a lookback
    window and block further trading on that symbol until cooldown expires.

    A winning trade MUST break the streak.

TESTS:
    5 tests covering loss clustering detection and cooldown.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from core.risk.protections.stoploss_guard import StoplossGuard
from core.risk.protections.base import ProtectionTrigger


class _Trade:
    """Trade object supporting both getattr and .get() access."""
    def __init__(self, symbol, profit, close_timestamp):
        self.symbol = symbol
        self.profit = profit
        self.close_timestamp = close_timestamp

    def get(self, key, default=None):
        return getattr(self, key, default)


def _trade(symbol, profit, minutes_ago):
    """Create a completed trade object."""
    return _Trade(
        symbol=symbol,
        profit=Decimal(str(profit)),
        close_timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class TestLossClusteringDetection:

    def test_no_trades_no_protection(self):
        """No completed trades → no protection."""
        guard = StoplossGuard(max_stoplosses=3)
        result = guard.check(symbol="SPY", completed_trades=[])
        assert result.is_protected is False

    def test_consecutive_losses_trigger_protection(self):
        """3 consecutive losses → symbol is protected."""
        guard = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(hours=1),
            cooldown_duration=timedelta(hours=1),
        )
        trades = [
            _trade("SPY", -1.00, 5),
            _trade("SPY", -0.50, 10),
            _trade("SPY", -2.00, 15),
        ]
        result = guard.check(symbol="SPY", completed_trades=trades)
        assert result.is_protected is True
        assert result.trigger == ProtectionTrigger.STOPLOSS_STREAK

    def test_win_breaks_streak(self):
        """A winning trade in the middle resets consecutive count."""
        guard = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(hours=1),
        )
        trades = [
            _trade("SPY", -1.00, 5),   # most recent: loss
            _trade("SPY", -0.50, 10),   # loss
            _trade("SPY", 1.00, 15),    # WIN — breaks streak
            _trade("SPY", -2.00, 20),   # older loss
        ]
        result = guard.check(symbol="SPY", completed_trades=trades)
        assert result.is_protected is False

    def test_outside_lookback_ignored(self):
        """Losses outside the lookback window are not counted."""
        guard = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(minutes=30),
        )
        # All losses are 60+ minutes ago — outside the 30-minute window
        trades = [
            _trade("SPY", -1.00, 60),
            _trade("SPY", -1.00, 65),
            _trade("SPY", -1.00, 70),
        ]
        result = guard.check(symbol="SPY", completed_trades=trades)
        assert result.is_protected is False

    def test_different_symbol_not_counted(self):
        """Losses on a different symbol don't count."""
        guard = StoplossGuard(max_stoplosses=3)
        trades = [
            _trade("QQQ", -1.00, 5),
            _trade("QQQ", -1.00, 10),
            _trade("QQQ", -1.00, 15),
        ]
        result = guard.check(symbol="SPY", completed_trades=trades)
        assert result.is_protected is False
