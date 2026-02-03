"""
P3-B2 — Daily Drawdown Limits

INVARIANT:
    DailyLossLimitProtection MUST block trading when intraday
    drawdown exceeds max_loss_usd. It MUST reset on a new trading day.

TESTS:
    5 tests covering daily loss enforcement.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, date

from core.risk.protections.daily_loss import DailyLossLimitProtection
from core.risk.protections.base import ProtectionContext


def _ctx(account_value, day=None, hour=10):
    d = day or date(2026, 1, 30)
    return ProtectionContext(
        now=datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=timezone.utc),
        account_value=Decimal(str(account_value)),
    )


class TestDailyDrawdownLimits:

    def test_allows_within_limit(self):
        """Trading allowed when drawdown < max_loss_usd."""
        p = DailyLossLimitProtection(max_loss_usd=Decimal("3.00"))
        result = p.check(_ctx(1000))
        assert result.allow is True

    def test_blocks_at_limit(self):
        """Trading blocked when drawdown >= max_loss_usd."""
        p = DailyLossLimitProtection(max_loss_usd=Decimal("3.00"))
        # First check sets start equity
        p.check(_ctx(1000))
        # Drawdown = 1000 - 997 = 3.00 (exactly at limit)
        result = p.check(_ctx(997))
        assert result.allow is False

    def test_blocks_beyond_limit(self):
        """Trading blocked when drawdown > max_loss_usd."""
        p = DailyLossLimitProtection(max_loss_usd=Decimal("3.00"))
        p.check(_ctx(1000))
        result = p.check(_ctx(995))
        assert result.allow is False

    def test_resets_on_new_day(self):
        """New trading day → drawdown resets, trading allowed."""
        p = DailyLossLimitProtection(max_loss_usd=Decimal("3.00"))
        # Day 1: breach limit
        p.check(_ctx(1000, day=date(2026, 1, 30)))
        result = p.check(_ctx(995, day=date(2026, 1, 30)))
        assert result.allow is False

        # Day 2: resets with new equity
        result = p.check(_ctx(995, day=date(2026, 1, 31)))
        assert result.allow is True

    def test_no_block_when_no_account_value(self):
        """If account_value is None → no block (fail-open)."""
        p = DailyLossLimitProtection(max_loss_usd=Decimal("3.00"))
        ctx = ProtectionContext(
            now=datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc),
            account_value=None,
        )
        result = p.check(ctx)
        assert result.allow is True
