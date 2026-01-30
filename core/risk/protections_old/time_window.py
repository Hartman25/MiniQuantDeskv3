"""
TradingWindowProtection

Blocks trading outside a configured time window (market local time).

This is intentionally duplicated with strategy time-gating:
- Strategy gate prevents signal spam
- Protection gate prevents ANY trade submission outside allowed windows
  (including bugs / other strategies)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from .base import ProtectionContext, ProtectionDecision


@dataclass
class TradingWindowProtection:
    name: str = "TradingWindow"
    tz: str = "America/New_York"
    start: time = time(10, 0)
    end: time = time(11, 30)

    def check(self, ctx: ProtectionContext) -> ProtectionDecision:
        local = ctx.now.astimezone(ZoneInfo(self.tz))
        t = local.time()
        if t < self.start or t > self.end:
            return ProtectionDecision(False, f"outside_window {self.start}-{self.end} {self.tz}")
        return ProtectionDecision(True)

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        return None

    def reset_day(self, day) -> None:
        return None
