"""
MaxTradesPerDayProtection

Counts trade submissions (entries and exits). For micro accounts, limiting
trade count is essential to avoid death-by-friction.

This protection is global (per-strategy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict

from .base import ProtectionContext, ProtectionDecision


@dataclass
class MaxTradesPerDayProtection:
    name: str = "MaxTradesPerDay"
    max_trades: int = 2

    _day: date = field(default_factory=lambda: date.min)
    _counts: Dict[str, int] = field(default_factory=dict)  # key = strategy

    def check(self, ctx: ProtectionContext) -> ProtectionDecision:
        d = ctx.now.date()
        if d != self._day:
            self.reset_day(d)

        c = self._counts.get(ctx.strategy, 0)
        if c >= self.max_trades:
            return ProtectionDecision(False, f"max_trades_reached {c}/{self.max_trades}")
        return ProtectionDecision(True)

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        d = ctx.now.date()
        if d != self._day:
            self.reset_day(d)
        self._counts[ctx.strategy] = self._counts.get(ctx.strategy, 0) + 1

    def reset_day(self, day) -> None:
        self._day = day
        self._counts = {}
