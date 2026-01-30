"""
DailyLossLimitProtection

Blocks trading when intraday drawdown exceeds a fixed USD threshold.
Uses account equity (account_value) to compute drawdown.

Fail-safe:
- If account_value is missing, this protection does NOT block.
  (You should still have RiskManager / PreTradeRiskGate limits)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .base import ProtectionContext, ProtectionDecision


@dataclass
class DailyLossLimitProtection:
    name: str = "DailyLossLimit"
    max_loss_usd: Decimal = Decimal("3.00")

    _day: date = field(default_factory=lambda: date.min)
    _start_equity: Optional[Decimal] = None

    def check(self, ctx: ProtectionContext) -> ProtectionDecision:
        if ctx.account_value is None:
            return ProtectionDecision(True, "no_account_value")

        d = ctx.now.date()
        if d != self._day:
            self.reset_day(d)
            self._start_equity = ctx.account_value

        if self._start_equity is None:
            self._start_equity = ctx.account_value

        dd = self._start_equity - ctx.account_value
        if dd >= self.max_loss_usd:
            return ProtectionDecision(
                False,
                f"daily_loss_limit hit dd={dd} start={self._start_equity} now={ctx.account_value}",
            )
        return ProtectionDecision(True)

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        return None

    def reset_day(self, day) -> None:
        self._day = day
        self._start_equity = None
