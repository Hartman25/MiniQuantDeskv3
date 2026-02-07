"""
PATCH 4 — Coordinator pure-step tests

INVARIANT:
    The RuntimeCoordinator's evaluate_signal() and guard functions are pure:
    they receive immutable snapshots and return Decision objects without
    performing any I/O.  Every guard can be tested in isolation.

TESTS:
    1.  evaluate_signal returns SUBMIT_MARKET for a valid BUY signal.
    2.  evaluate_signal returns SUBMIT_LIMIT for a valid LIMIT signal.
    3.  Zero-qty signal → SKIP (QTY_ZERO).
    4.  Single-trade guard blocks entry when position exists.
    5.  Single-trade guard allows exit when position exists.
    6.  Cooldown guard blocks when elapsed < cooldown.
    7.  Cooldown guard allows when elapsed >= cooldown.
    8.  Protection block → SKIP.
    9.  Risk block → SKIP.
   10.  Sell with no position → SKIP (NO_POSITION_TO_SELL).
   11.  Sell qty capped to position size.
   12.  Buy when already in position → SKIP (POSITION_EXISTS).
   13.  LIMIT without limit_price → SKIP.
   14.  apply_risk_qty extracts approved_qty.
   15.  CycleResult accumulates decisions.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import pytest

from core.runtime.coordinator import (
    Action,
    CycleResult,
    GuardResult,
    MarketSnapshot,
    SignalDecision,
    SignalSnapshot,
    SkipReason,
    apply_risk_qty,
    cap_sell_qty,
    check_cooldown,
    check_position_for_sell,
    check_single_trade,
    evaluate_signal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(
    *,
    side="BUY",
    qty="10",
    order_type="MARKET",
    limit_price=None,
    is_exit=False,
    strategy="TestStrat",
    symbol="SPY",
) -> SignalSnapshot:
    return SignalSnapshot(
        trade_id="t_test_001",
        strategy=strategy,
        symbol=symbol,
        side=side,
        quantity=Decimal(qty),
        price=Decimal("100"),
        order_type=order_type,
        limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
        is_exit=is_exit,
    )


def _market(
    *,
    has_position=False,
    position_qty="0",
    has_open_order=False,
    symbol="SPY",
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        account_value=Decimal("100000"),
        buying_power=Decimal("50000"),
        has_position=has_position,
        position_qty=Decimal(position_qty),
        has_open_order=has_open_order,
    )


@dataclass
class _FakeRisk:
    approved: bool = True
    reason: str = ""
    approved_qty: Optional[Decimal] = None

    def to_dict(self):
        return {}


@dataclass
class _FakeProtection:
    is_protected: bool = False
    reason: str = ""
    until: object = None


# ---------------------------------------------------------------------------
# Tests: evaluate_signal
# ---------------------------------------------------------------------------

class TestEvaluateSignalHappyPath:
    """Valid signals produce SUBMIT decisions."""

    def test_market_buy(self):
        d = evaluate_signal(
            _signal(side="BUY"),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SUBMIT_MARKET
        assert d.final_qty == Decimal("10")
        assert d.skip_reason is None

    def test_limit_buy(self):
        d = evaluate_signal(
            _signal(side="BUY", order_type="LIMIT", limit_price="99.50"),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SUBMIT_LIMIT
        assert d.final_qty == Decimal("10")


class TestQtyGuard:
    """Zero or negative qty signals are skipped."""

    def test_zero_qty(self):
        d = evaluate_signal(
            _signal(qty="0"),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.QTY_ZERO

    def test_negative_qty(self):
        d = evaluate_signal(
            _signal(qty="-5"),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.QTY_ZERO


class TestSingleTradeGuard:
    """Single-trade-at-a-time blocks entries, allows exits."""

    def test_blocks_entry_with_position(self):
        d = evaluate_signal(
            _signal(side="BUY"),
            _market(has_position=True, position_qty="5"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        # Could be SINGLE_TRADE_BLOCK or POSITION_EXISTS
        assert d.skip_reason in (SkipReason.SINGLE_TRADE_BLOCK, SkipReason.POSITION_EXISTS)

    def test_blocks_entry_with_open_order(self):
        d = evaluate_signal(
            _signal(side="BUY"),
            _market(has_open_order=True),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.SINGLE_TRADE_BLOCK

    def test_allows_exit_with_position(self):
        d = evaluate_signal(
            _signal(side="SELL", is_exit=True),
            _market(has_position=True, position_qty="5"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SUBMIT_MARKET


class TestCooldownGuard:
    """Cooldown blocks signals within the window."""

    def test_blocks_within_cooldown(self):
        now = time.time()
        last = {("TestStrat", "SPY", "BUY"): now - 5}  # 5s ago

        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=30,
            last_action_ts=last,
            now_ts=now,
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.COOLDOWN

    def test_allows_after_cooldown(self):
        now = time.time()
        last = {("TestStrat", "SPY", "BUY"): now - 60}  # 60s ago

        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=30,
            last_action_ts=last,
            now_ts=now,
        )
        assert d.action == Action.SUBMIT_MARKET

    def test_zero_cooldown_always_allows(self):
        now = time.time()
        last = {("TestStrat", "SPY", "BUY"): now}  # right now

        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=0,
            last_action_ts=last,
            now_ts=now,
        )
        assert d.action == Action.SUBMIT_MARKET


class TestProtectionGuard:
    """Protection manager blocks → SKIP."""

    def test_protection_block(self):
        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
            protection_result=_FakeProtection(is_protected=True, reason="drawdown"),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.PROTECTION_BLOCK

    def test_protection_allows(self):
        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
            protection_result=_FakeProtection(is_protected=False),
        )
        assert d.action == Action.SUBMIT_MARKET


class TestRiskGuard:
    """Risk manager rejection → SKIP."""

    def test_risk_block(self):
        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
            risk_result=_FakeRisk(approved=False, reason="max_exposure"),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.RISK_BLOCK

    def test_risk_approved(self):
        d = evaluate_signal(
            _signal(),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
            risk_result=_FakeRisk(approved=True),
        )
        assert d.action == Action.SUBMIT_MARKET


class TestSellGuards:
    """Sell-specific guards."""

    def test_sell_with_no_position(self):
        d = evaluate_signal(
            _signal(side="SELL"),
            _market(has_position=False, position_qty="0"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.NO_POSITION_TO_SELL

    def test_sell_qty_capped_to_position(self):
        # SELL with is_exit=True to pass single-trade guard
        d = evaluate_signal(
            _signal(side="SELL", qty="100", is_exit=True),
            _market(has_position=True, position_qty="5"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SUBMIT_MARKET
        assert d.final_qty == Decimal("5")

    def test_sell_exact_position(self):
        d = evaluate_signal(
            _signal(side="SELL", qty="5", is_exit=True),
            _market(has_position=True, position_qty="5"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SUBMIT_MARKET
        assert d.final_qty == Decimal("5")


class TestBuyPositionExists:
    """Buy when already in position → SKIP."""

    def test_buy_blocked_by_existing_position(self):
        d = evaluate_signal(
            _signal(side="BUY"),
            _market(has_position=True, position_qty="5"),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason in (SkipReason.POSITION_EXISTS, SkipReason.SINGLE_TRADE_BLOCK)


class TestLimitMissingPrice:
    """LIMIT order without limit_price → SKIP."""

    def test_limit_no_price(self):
        d = evaluate_signal(
            _signal(order_type="LIMIT", limit_price=None),
            _market(),
            cooldown_s=0,
            last_action_ts={},
            now_ts=time.time(),
        )
        assert d.action == Action.SKIP
        assert d.skip_reason == SkipReason.LIMIT_MISSING_PRICE


# ---------------------------------------------------------------------------
# Tests: pure helper functions
# ---------------------------------------------------------------------------

class TestApplyRiskQty:
    """apply_risk_qty extracts the correct attribute."""

    def test_approved_qty(self):
        r = _FakeRisk(approved=True, approved_qty=Decimal("7"))
        assert apply_risk_qty(r, Decimal("10"), "BUY") == Decimal("7")

    def test_no_approved_qty_returns_original(self):
        r = _FakeRisk(approved=True)
        assert apply_risk_qty(r, Decimal("10"), "BUY") == Decimal("10")

    def test_sell_ignores_approved_qty(self):
        r = _FakeRisk(approved=True, approved_qty=Decimal("7"))
        assert apply_risk_qty(r, Decimal("10"), "SELL") == Decimal("10")


class TestCapSellQty:
    """cap_sell_qty limits to position."""

    def test_caps(self):
        assert cap_sell_qty(Decimal("100"), Decimal("5")) == Decimal("5")

    def test_no_cap_needed(self):
        assert cap_sell_qty(Decimal("3"), Decimal("5")) == Decimal("3")


class TestCheckCooldownPure:
    """check_cooldown is pure — no side effects."""

    def test_blocked(self):
        r = check_cooldown(
            strategy="S", symbol="SPY", side="BUY",
            now_ts=100.0,
            last_action_ts={("S", "SPY", "BUY"): 95.0},
            cooldown_s=30,
        )
        assert not r.allowed
        assert r.reason == SkipReason.COOLDOWN

    def test_allowed(self):
        r = check_cooldown(
            strategy="S", symbol="SPY", side="BUY",
            now_ts=200.0,
            last_action_ts={("S", "SPY", "BUY"): 95.0},
            cooldown_s=30,
        )
        assert r.allowed


class TestCheckSingleTradePure:
    """check_single_trade is pure."""

    def test_blocks_entry_with_position(self):
        r = check_single_trade(is_exit=False, has_position=True, has_open_order=False)
        assert not r.allowed

    def test_allows_exit(self):
        r = check_single_trade(is_exit=True, has_position=True, has_open_order=True)
        assert r.allowed

    def test_allows_entry_no_conflicts(self):
        r = check_single_trade(is_exit=False, has_position=False, has_open_order=False)
        assert r.allowed


class TestCycleResult:
    """CycleResult accumulates decisions."""

    def test_accumulation(self):
        cr = CycleResult()
        d1 = SignalDecision(action=Action.SUBMIT_MARKET, final_qty=Decimal("10"), final_side="BUY")
        d2 = SignalDecision(action=Action.SKIP, skip_reason=SkipReason.COOLDOWN)

        cr.decisions.append(d1)
        cr.decisions.append(d2)
        cr.symbols_processed = 1
        cr.signals_evaluated = 2
        cr.orders_intended = 1
        cr.skipped = 1

        assert len(cr.decisions) == 2
        assert cr.orders_intended == 1
        assert cr.skipped == 1
