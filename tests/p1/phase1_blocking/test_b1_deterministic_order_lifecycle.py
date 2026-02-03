"""
P1-B1 — Deterministic Order Lifecycle

INVARIANT:
    The order state machine MUST:
    1. Reject any transition not in the VALID_TRANSITIONS registry
       (raises InvalidTransitionError).
    2. Reject any transition FROM a terminal state
       (raises TerminalStateError).
    3. Accept ONLY the 9 explicitly enumerated transitions.

    These properties guarantee that the order lifecycle is fully
    deterministic: no code path can silently move an order into
    an unexpected state.

HALT DECISION:
    Invalid transition → raise (caller MUST handle).  This is correct
    because silently swallowing an illegal transition could leave the
    system in an inconsistent state (e.g. double-fill, phantom cancel).
"""

import pytest
from decimal import Decimal

from core.state.order_machine import (
    OrderStateMachine,
    OrderStatus,
    InvalidTransitionError,
    TerminalStateError,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def order_machine(tmp_path):
    """Fresh OrderStateMachine with started event bus."""
    from core.events.bus import OrderEventBus
    from core.state.transaction_log import TransactionLog

    bus = OrderEventBus()
    bus.start()
    txlog = TransactionLog(path=tmp_path / "b1_txn.ndjson")
    sm = OrderStateMachine(event_bus=bus, transaction_log=txlog)

    yield sm

    txlog.close()
    bus.stop()


def _create_and_submit(sm, order_id="ORD-B1", symbol="SPY"):
    """Helper: create order and transition to SUBMITTED."""
    sm.create_order(
        order_id=order_id,
        symbol=symbol,
        quantity=Decimal("10"),
        side="LONG",
        order_type="MARKET",
        strategy="TestStrat",
    )
    sm.transition(
        order_id=order_id,
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BRK-001",
    )


def _create_submit_fill(sm, order_id="ORD-B1", symbol="SPY"):
    """Helper: create → submit → fill."""
    _create_and_submit(sm, order_id, symbol)
    sm.transition(
        order_id=order_id,
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.FILLED,
        broker_order_id="BRK-001",
        filled_qty=Decimal("10"),
        fill_price=Decimal("450.00"),
    )


# ===================================================================
# 1. Terminal state immutability
# ===================================================================

class TestTerminalStateImmutability:
    """Once an order reaches a terminal state (FILLED, CANCELLED,
    REJECTED, EXPIRED), NO further transitions are allowed."""

    def test_filled_order_rejects_further_transition(self, order_machine):
        _create_submit_fill(order_machine)

        with pytest.raises(TerminalStateError):
            order_machine.transition(
                order_id="ORD-B1",
                from_state=OrderStatus.FILLED,
                to_state=OrderStatus.CANCELLED,
                broker_order_id="BRK-001",
            )

    def test_cancelled_order_rejects_further_transition(self, order_machine):
        _create_and_submit(order_machine)
        order_machine.transition(
            order_id="ORD-B1",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.CANCELLED,
            broker_order_id="BRK-001",
            reason="user_cancel",
        )

        with pytest.raises(TerminalStateError):
            order_machine.transition(
                order_id="ORD-B1",
                from_state=OrderStatus.CANCELLED,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK-001",
            )

    def test_rejected_order_rejects_further_transition(self, order_machine):
        order_machine.create_order(
            order_id="ORD-REJ",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )
        order_machine.transition(
            order_id="ORD-REJ",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.REJECTED,
            reason="risk_gate",
        )

        with pytest.raises(TerminalStateError):
            order_machine.transition(
                order_id="ORD-REJ",
                from_state=OrderStatus.REJECTED,
                to_state=OrderStatus.PENDING,
            )


# ===================================================================
# 2. Invalid transition rejection
# ===================================================================

class TestInvalidTransitionRejection:
    """Transitions NOT in VALID_TRANSITIONS must raise InvalidTransitionError."""

    def test_pending_to_filled_rejected(self, order_machine):
        """PENDING → FILLED is not valid (must go through SUBMITTED first)."""
        order_machine.create_order(
            order_id="ORD-INV1",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        with pytest.raises(InvalidTransitionError):
            order_machine.transition(
                order_id="ORD-INV1",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.FILLED,
                broker_order_id="BRK-X",
                filled_qty=Decimal("5"),
                fill_price=Decimal("100"),
            )

    def test_pending_to_cancelled_rejected(self, order_machine):
        """PENDING → CANCELLED is not valid (must be SUBMITTED first)."""
        order_machine.create_order(
            order_id="ORD-INV2",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="TestStrat",
        )

        with pytest.raises(InvalidTransitionError):
            order_machine.transition(
                order_id="ORD-INV2",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.CANCELLED,
                broker_order_id="BRK-X",
            )

    def test_submitted_to_pending_rejected(self, order_machine):
        """SUBMITTED → PENDING is never valid (no backward transitions)."""
        _create_and_submit(order_machine, order_id="ORD-INV3")

        with pytest.raises(InvalidTransitionError):
            order_machine.transition(
                order_id="ORD-INV3",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.PENDING,
            )

    def test_filled_to_submitted_rejected(self, order_machine):
        """FILLED → SUBMITTED is never valid (terminal + backward)."""
        _create_submit_fill(order_machine, order_id="ORD-INV4")

        with pytest.raises(TerminalStateError):
            order_machine.transition(
                order_id="ORD-INV4",
                from_state=OrderStatus.FILLED,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK-X",
            )


# ===================================================================
# 3. All valid transitions are accepted
# ===================================================================

class TestAllValidTransitionsAccepted:
    """Every transition in VALID_TRANSITIONS must succeed when preconditions met."""

    def test_pending_to_submitted(self, order_machine):
        order_machine.create_order(
            order_id="ORD-V1", symbol="SPY", quantity=Decimal("1"),
            side="LONG", order_type="MARKET", strategy="T",
        )
        order_machine.transition(
            order_id="ORD-V1",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK-1",
        )
        assert order_machine.get_order("ORD-V1").state == OrderStatus.SUBMITTED

    def test_submitted_to_filled(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V2")
        order_machine.transition(
            order_id="ORD-V2",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK-1",
            filled_qty=Decimal("10"),
            fill_price=Decimal("100"),
        )
        assert order_machine.get_order("ORD-V2").state == OrderStatus.FILLED

    def test_submitted_to_partially_filled(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V3")
        order_machine.transition(
            order_id="ORD-V3",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.PARTIALLY_FILLED,
            broker_order_id="BRK-1",
            filled_qty=Decimal("5"),
            fill_price=Decimal("100"),
        )
        assert order_machine.get_order("ORD-V3").state == OrderStatus.PARTIALLY_FILLED

    def test_partially_filled_to_filled(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V4")
        order_machine.transition(
            order_id="ORD-V4",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.PARTIALLY_FILLED,
            broker_order_id="BRK-1",
            filled_qty=Decimal("5"),
            fill_price=Decimal("100"),
        )
        order_machine.transition(
            order_id="ORD-V4",
            from_state=OrderStatus.PARTIALLY_FILLED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK-1",
            filled_qty=Decimal("10"),
            fill_price=Decimal("100"),
        )
        assert order_machine.get_order("ORD-V4").state == OrderStatus.FILLED

    def test_submitted_to_cancelled(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V5")
        order_machine.transition(
            order_id="ORD-V5",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.CANCELLED,
            broker_order_id="BRK-1",
            reason="timeout",
        )
        assert order_machine.get_order("ORD-V5").state == OrderStatus.CANCELLED

    def test_partially_filled_to_cancelled(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V6")
        order_machine.transition(
            order_id="ORD-V6",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.PARTIALLY_FILLED,
            broker_order_id="BRK-1",
            filled_qty=Decimal("3"),
            fill_price=Decimal("100"),
        )
        order_machine.transition(
            order_id="ORD-V6",
            from_state=OrderStatus.PARTIALLY_FILLED,
            to_state=OrderStatus.CANCELLED,
            broker_order_id="BRK-1",
            reason="timeout",
        )
        assert order_machine.get_order("ORD-V6").state == OrderStatus.CANCELLED

    def test_submitted_to_rejected(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V7")
        order_machine.transition(
            order_id="ORD-V7",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.REJECTED,
            broker_order_id="BRK-1",
            reason="broker_reject",
        )
        assert order_machine.get_order("ORD-V7").state == OrderStatus.REJECTED

    def test_pending_to_rejected(self, order_machine):
        order_machine.create_order(
            order_id="ORD-V8", symbol="SPY", quantity=Decimal("1"),
            side="LONG", order_type="MARKET", strategy="T",
        )
        order_machine.transition(
            order_id="ORD-V8",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.REJECTED,
            reason="risk_gate",
        )
        assert order_machine.get_order("ORD-V8").state == OrderStatus.REJECTED

    def test_submitted_to_expired(self, order_machine):
        _create_and_submit(order_machine, order_id="ORD-V9")
        order_machine.transition(
            order_id="ORD-V9",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.EXPIRED,
        )
        assert order_machine.get_order("ORD-V9").state == OrderStatus.EXPIRED
