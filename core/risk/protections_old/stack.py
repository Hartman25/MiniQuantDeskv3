"""
ProtectionStack - orchestrates a set of protections.

Fail-safe behavior:
- If a protection raises, we BLOCK the trade (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import logging

from .base import IProtection, ProtectionContext

logger = logging.getLogger(__name__)


@dataclass
class StackDecision:
    allowed: bool
    reasons: List[str]

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


class ProtectionStack:
    def __init__(self, protections: Optional[List[IProtection]] = None):
        self._protections: List[IProtection] = protections or []

    def add(self, prot: IProtection) -> None:
        self._protections.append(prot)

    def check(self, ctx: ProtectionContext) -> StackDecision:
        reasons: List[str] = []
        for prot in self._protections:
            try:
                dec = prot.check(ctx)
            except Exception as e:
                # Fail-closed
                msg = f"{getattr(prot, 'name', prot.__class__.__name__)}_ERROR:{e}"
                logger.exception(msg)
                return StackDecision(False, [msg])

            if not dec.allowed:
                reasons.append(f"{prot.name}:{dec.reason or 'BLOCKED'}")

        return StackDecision(len(reasons) == 0, reasons)

    def on_trade_submitted(self, ctx: ProtectionContext) -> None:
        for prot in self._protections:
            try:
                prot.on_trade_submitted(ctx)
            except Exception:
                logger.exception(
                    "Protection on_trade_submitted error for %s",
                    getattr(prot, "name", prot.__class__.__name__),
                )

    def reset_day(self, day) -> None:
        for prot in self._protections:
            try:
                prot.reset_day(day)
            except Exception:
                logger.exception(
                    "Protection reset_day error for %s",
                    getattr(prot, "name", prot.__class__.__name__),
                )
