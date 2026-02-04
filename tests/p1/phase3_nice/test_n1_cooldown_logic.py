"""
P3-N1 — Cooldown Logic

INVARIANT:
    The CooldownPeriod protection MUST block all trading after a major
    loss exceeding the loss_threshold. It MUST be inactive when no
    major loss has occurred.

TESTS:
    4 tests covering cooldown activation and reset.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from core.risk.protections.cooldown import CooldownPeriod


class _Trade:
    def __init__(self, symbol, profit, close_timestamp):
        self.symbol = symbol
        self.profit = profit
        self.close_timestamp = close_timestamp

    def get(self, key, default=None):
        return getattr(self, key, default)


def _trade(profit, minutes_ago):
    """Create a completed trade object."""
    return _Trade(
        symbol='SPY',
        profit=Decimal(str(profit)),
        close_timestamp=datetime.now(timezone.utc) - timedelta(seconds=minutes_ago * 60 - 5),
    )


class TestCooldownLogic:

    def test_no_cooldown_without_trades(self):
        """No completed trades → no cooldown."""
        cd = CooldownPeriod(loss_threshold=Decimal("5.00"))
        result = cd.check(completed_trades=[])
        assert result.is_protected is False

    def test_cooldown_triggers_on_major_loss(self):
        """Loss >= threshold → cooldown triggered."""
        cd = CooldownPeriod(
            loss_threshold=Decimal("5.00"),
            cooldown_duration=timedelta(minutes=30),
        )
        # Reset _last_check_time to before the trade
        cd._last_check_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        trades = [_trade(-6.00, 1)]  # $6 loss, 1 minute ago
        result = cd.check(completed_trades=trades)
        assert result.is_protected is True

    def test_no_cooldown_on_small_loss(self):
        """Loss < threshold → no cooldown."""
        cd = CooldownPeriod(
            loss_threshold=Decimal("5.00"),
        )
        cd._last_check_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        trades = [_trade(-2.00, 1)]  # $2 loss < $5 threshold
        result = cd.check(completed_trades=trades)
        assert result.is_protected is False

    def test_reset_resets_check_time(self):
        """reset() resets _last_check_time so old trades are not re-checked."""
        cd = CooldownPeriod(
            loss_threshold=Decimal("5.00"),
            cooldown_duration=timedelta(seconds=0),  # instant cooldown
        )
        cd._last_check_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        trades = [_trade(-10.00, 1)]
        cd.check(completed_trades=trades)
        cd.reset()
        # After reset, _last_check_time is updated to now
        # so the same trades won't trigger again
        assert cd._last_check_time is not None
        result = cd.check(completed_trades=trades)
        assert result.is_protected is False
