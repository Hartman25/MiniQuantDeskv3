"""
Risk Protections Stack (Freqtrade-inspired).

These are *global trading halts / guardrails* applied BEFORE order submission.

Protections are distinct from:
- Strategy logic (signal generation)
- PreTradeRiskGate / RiskManager (order legality & portfolio limits)

Protections answer: "Should we trade at all right now?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, Protocol


@dataclass(frozen=True)
class ProtectionDecision:
    allowed: bool
    reason: str = ""
    meta: Optional[Dict[str, Any]] = None


@dataclass
class ProtectionContext:
    now: datetime
    symbol: str
    strategy: str
    side: str
    price: Decimal
    quantity: Decimal
    account_value: Optional[Decimal] = None
    # Optional rolling metrics
    vwap: Optional[Decimal] = None
    # Caller can pass arbitrary state (e.g., recent bars)
    extra: Optional[Dict[str, Any]] = None


class IProtection(Protocol):
    name: str

    def check(self, ctx: ProtectionContext) -> ProtectionDecision: ...
    def on_trade_submitted(self, ctx: ProtectionContext) -> None: ...
    def reset_day(self, day) -> None: ...
