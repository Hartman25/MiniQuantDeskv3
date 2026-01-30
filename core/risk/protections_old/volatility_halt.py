"""
VolatilityHaltProtection

Blocks trading when realized volatility over a short window exceeds a threshold.
This reduces the chance a micro account gets wiped on spike/whipsaw.

Caller must provide recent returns in ctx.extra["recent_returns"] as a list[Decimal].
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import ProtectionContext, ProtectionDecision


@dataclass
class VolatilityHaltProtection:
    name: str = "VolatilityHalt"
    max_std: Decimal = Decimal("0.006")  # 0.6% std over window
    min_points: int = 20

    def check(self, ctx: ProtectionContext) -> ProtectionDecision:
        extra = ctx.extra or {}
        rets = extra.get("recent_returns")
        if not rets or not isinstance(rets, list) or len(rets) < self.min_points:
            return ProtectionDecision(True, "insufficient_returns")

        # Compute stddev in Decimal (simple, not optimized)
        vals = [Decimal(str(x)) for x in rets[-self.min_points:]]
        mean = sum(vals) / Decimal(len(vals))
        var = sum((v - mean) * (v - mean) for v in vals) / Decimal(len(vals))
        # crude sqrt via float - acceptable for a protection threshold
        std = Decimal(str((float(var)) ** 0.5))

        if std >= self.max_std:
            return ProtectionDecision(False, f"vol_halt std={std} >= {self.max_std}")
        return ProtectionDecision(True)

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        return None

    def reset_day(self, day) -> None:
        return None
