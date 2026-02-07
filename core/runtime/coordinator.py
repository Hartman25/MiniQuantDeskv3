"""
Runtime Coordinator — pure-function decomposition of the trading loop.

PATCH 4: Extracts the per-cycle decision logic from app.run() into
testable, pure steps.  The coordinator receives market state and returns
Decision objects that the outer loop then executes.

ARCHITECTURE:
    run()  ──►  RuntimeCoordinator.step(snapshot)  ──►  CycleResult
                     │
                     ├── evaluate_signal()  ──►  SignalDecision
                     ├── check_guards()     ──►  GuardResult
                     └── build_order()      ──►  OrderIntent

    The coordinator never touches the broker, journal, or any I/O.
    All side-effects remain in the outer run() loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Value objects (pure data, no I/O)
# ---------------------------------------------------------------------------

class Action(str, Enum):
    """What the outer loop should do with this decision."""
    SUBMIT_MARKET = "SUBMIT_MARKET"
    SUBMIT_LIMIT = "SUBMIT_LIMIT"
    SKIP = "SKIP"                  # blocked by a guard
    NO_SIGNAL = "NO_SIGNAL"        # strategy produced nothing


class SkipReason(str, Enum):
    """Why a signal was skipped (guard that fired)."""
    NO_SIGNAL = "no_signal"
    QTY_ZERO = "qty_zero"
    SINGLE_TRADE_BLOCK = "single_trade_block"
    COOLDOWN = "cooldown"
    PROTECTION_BLOCK = "protection_block"
    RISK_BLOCK = "risk_block"
    POSITION_EXISTS = "position_exists"
    NO_POSITION_TO_SELL = "no_position_to_sell"
    QTY_NONPOSITIVE_AFTER_RISK = "qty_nonpositive_after_risk"
    LIMIT_MISSING_PRICE = "limit_missing_price"
    MARKET_DATA_ERROR = "market_data_error"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable snapshot of one signal from a strategy."""
    trade_id: str
    strategy: str
    symbol: str
    side: str           # "BUY" | "SELL"
    quantity: Decimal
    price: Decimal      # reference price (close or limit)
    order_type: str     # "MARKET" | "LIMIT"
    limit_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    ttl_seconds: int = 90
    is_exit: bool = False
    raw: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class MarketSnapshot:
    """Immutable market state for one symbol at decision time."""
    symbol: str
    account_value: Decimal
    buying_power: Decimal
    has_position: bool
    position_qty: Decimal
    has_open_order: bool


@dataclass(frozen=True)
class GuardResult:
    """Result of running all pre-trade guards."""
    allowed: bool
    reason: Optional[SkipReason] = None
    detail: str = ""


@dataclass(frozen=True)
class SignalDecision:
    """
    Pure decision for one signal — what to do and why.

    The outer loop reads `action` and dispatches accordingly:
      SUBMIT_MARKET  → engine.submit_market_order(...)
      SUBMIT_LIMIT   → engine.submit_limit_order(...)
      SKIP           → log skip_reason + continue
      NO_SIGNAL      → do nothing
    """
    action: Action
    signal: Optional[SignalSnapshot] = None
    skip_reason: Optional[SkipReason] = None
    skip_detail: str = ""
    # Final order parameters (after risk adjustments)
    final_qty: Decimal = Decimal("0")
    final_side: str = ""
    internal_order_id: str = ""


@dataclass
class CycleResult:
    """Result of one full coordinator step (all symbols, all signals)."""
    decisions: List[SignalDecision] = field(default_factory=list)
    symbols_processed: int = 0
    signals_evaluated: int = 0
    orders_intended: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------
# Pure guard functions
# ---------------------------------------------------------------------------

def check_cooldown(
    *,
    strategy: str,
    symbol: str,
    side: str,
    now_ts: float,
    last_action_ts: Dict,
    cooldown_s: int,
) -> GuardResult:
    """Pure cooldown check — no side effects."""
    if cooldown_s <= 0:
        return GuardResult(allowed=True)

    key = (strategy or "UNKNOWN", symbol, side.upper())
    last = float(last_action_ts.get(key, 0.0) or 0.0)
    elapsed = now_ts - last

    if elapsed < cooldown_s:
        return GuardResult(
            allowed=False,
            reason=SkipReason.COOLDOWN,
            detail=f"elapsed={elapsed:.1f}s < cooldown={cooldown_s}s",
        )
    return GuardResult(allowed=True)


def check_single_trade(
    *,
    is_exit: bool,
    has_position: bool,
    has_open_order: bool,
) -> GuardResult:
    """Pure single-trade-at-a-time guard — no side effects."""
    if is_exit:
        # Exits are always allowed through the single-trade guard
        return GuardResult(allowed=True)

    if has_position or has_open_order:
        return GuardResult(
            allowed=False,
            reason=SkipReason.SINGLE_TRADE_BLOCK,
            detail=f"has_position={has_position} has_open_order={has_open_order}",
        )
    return GuardResult(allowed=True)


def check_position_for_sell(
    *,
    side: str,
    position_qty: Decimal,
) -> GuardResult:
    """Verify we have a position to sell (pure)."""
    if side not in ("SELL", "SHORT"):
        return GuardResult(allowed=True)

    if position_qty <= 0:
        return GuardResult(
            allowed=False,
            reason=SkipReason.NO_POSITION_TO_SELL,
            detail=f"position_qty={position_qty}",
        )
    return GuardResult(allowed=True)


def cap_sell_qty(qty: Decimal, position_qty: Decimal) -> Decimal:
    """Cap sell quantity to position size (pure)."""
    if qty > position_qty > 0:
        return position_qty
    return qty


def apply_risk_qty(
    risk_result,
    original_qty: Decimal,
    side: str,
) -> Decimal:
    """
    Extract risk-approved quantity from risk result (pure).

    Tries several attribute names that different risk implementations use.
    """
    if side not in ("BUY", "LONG"):
        return original_qty

    for attr in (
        "approved_qty",
        "approved_quantity",
        "capped_qty",
        "capped_quantity",
        "sized_qty",
        "sized_quantity",
    ):
        v = getattr(risk_result, attr, None)
        if v is not None:
            try:
                return Decimal(str(v))
            except (InvalidOperation, TypeError, ValueError):
                continue

    return original_qty


def evaluate_signal(
    signal: SignalSnapshot,
    market: MarketSnapshot,
    *,
    cooldown_s: int,
    last_action_ts: Dict,
    now_ts: float,
    protection_result=None,
    risk_result=None,
) -> SignalDecision:
    """
    Pure evaluation of one signal against all guards.

    Returns a SignalDecision with the action to take.
    Does NOT perform any I/O.
    """
    # Guard 1: quantity must be positive
    if signal.quantity <= 0:
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=SkipReason.QTY_ZERO,
        )

    # Guard 2: single-trade-at-a-time (entries only)
    stg = check_single_trade(
        is_exit=signal.is_exit,
        has_position=market.has_position,
        has_open_order=market.has_open_order,
    )
    if not stg.allowed:
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=stg.reason,
            skip_detail=stg.detail,
        )

    # Guard 3: cooldown
    cd = check_cooldown(
        strategy=signal.strategy,
        symbol=signal.symbol,
        side=signal.side,
        now_ts=now_ts,
        last_action_ts=last_action_ts,
        cooldown_s=cooldown_s,
    )
    if not cd.allowed:
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=cd.reason,
            skip_detail=cd.detail,
        )

    # Guard 4: protections
    if protection_result is not None and getattr(protection_result, "is_protected", False):
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=SkipReason.PROTECTION_BLOCK,
            skip_detail=getattr(protection_result, "reason", ""),
        )

    # Guard 5: risk
    if risk_result is not None and not getattr(risk_result, "approved", True):
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=SkipReason.RISK_BLOCK,
            skip_detail=getattr(risk_result, "reason", ""),
        )

    # Compute final qty
    qty = signal.quantity
    if risk_result is not None:
        qty = apply_risk_qty(risk_result, qty, signal.side)

    # Position guard for sells
    if signal.side in ("SELL", "SHORT"):
        pg = check_position_for_sell(side=signal.side, position_qty=market.position_qty)
        if not pg.allowed:
            return SignalDecision(
                action=Action.SKIP,
                signal=signal,
                skip_reason=pg.reason,
                skip_detail=pg.detail,
            )
        qty = cap_sell_qty(qty, market.position_qty)

    # Already-in-position guard for buys
    if signal.side in ("BUY", "LONG") and market.has_position and market.position_qty > 0:
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=SkipReason.POSITION_EXISTS,
            skip_detail=f"position_qty={market.position_qty}",
        )

    if qty <= 0:
        return SignalDecision(
            action=Action.SKIP,
            signal=signal,
            skip_reason=SkipReason.QTY_NONPOSITIVE_AFTER_RISK,
        )

    # Determine order type
    if signal.order_type == "LIMIT":
        if signal.limit_price is None:
            return SignalDecision(
                action=Action.SKIP,
                signal=signal,
                skip_reason=SkipReason.LIMIT_MISSING_PRICE,
            )
        action = Action.SUBMIT_LIMIT
    else:
        action = Action.SUBMIT_MARKET

    return SignalDecision(
        action=action,
        signal=signal,
        final_qty=qty,
        final_side=signal.side,
    )
