"""
Typed strategy signal objects.

Why:
- Dict signals are fragile (silent key typos)
- Strong typing improves validation + testability
- Still supports legacy dict consumption via to_dict()

This module intentionally stays small and dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import Optional, Dict, Any, Literal


Side = Literal["BUY", "SELL"]

class SignalType(str, Enum):
    """Runtime-friendly signal side enum (string-valued)."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class StrategySignal:
    """
    Strategy output = intent to trade (NOT an order).

    Notes:
    - side is BUY/SELL (runner/execution decides how to route)
    - quantity is Decimal to support fractional shares
    - stop_loss / take_profit are OPTIONAL prices (not percentages)
    - reason is REQUIRED for auditability
    """
    symbol: str
    side: Side
    quantity: Decimal
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    entry_price: Optional[Decimal] = None
    limit_price: Optional[Decimal] = None
    ttl_seconds: Optional[int] = None  # Optional time-to-live for LIMIT orders ("one attempt only")
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    reason: str = ""
    strategy: str = "UNKNOWN"


    def __post_init__(self) -> None:
        # Accept SignalType enum values as well as raw strings.
        if isinstance(self.side, SignalType):
            object.__setattr__(self, 'side', self.side.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol.upper(),
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "entry_price": self.entry_price,
            "limit_price": self.limit_price,
            "ttl_seconds": self.ttl_seconds,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
            "strategy": self.strategy,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "StrategySignal":
        return StrategySignal(
            symbol=str(d["symbol"]).upper(),
            side=str(d["side"]).upper(),  # type: ignore
            quantity=Decimal(str(d["quantity"])),
            order_type=str(d.get("order_type", "MARKET")).upper(),  # type: ignore
            entry_price=Decimal(str(d["entry_price"])) if d.get("entry_price") is not None else None,
            limit_price=Decimal(str(d["limit_price"])) if d.get("limit_price") is not None else None,
            ttl_seconds=int(d.get("ttl_seconds")) if d.get("ttl_seconds") is not None else None,
            stop_loss=Decimal(str(d["stop_loss"])) if d.get("stop_loss") is not None else None,
            take_profit=Decimal(str(d["take_profit"])) if d.get("take_profit") is not None else None,
            reason=str(d.get("reason", "")),
            strategy=str(d.get("strategy", "UNKNOWN")),
        )

# Back-compat alias (older code/tests expect these names)
TradingSignal = StrategySignal
