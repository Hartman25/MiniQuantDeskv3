"""
State management components for order and position persistence.

Provides:
- OrderStateMachine: Explicit state transitions with guards
- TransactionLog: Append-only event log for crash recovery
- PositionStore: SQLite-backed position persistence
"""

from .order_machine import (
    OrderStateMachine,
    OrderStatus,
    OrderTransition,
    OrderStateChangedEvent,
    OrderStateMachineError,
    InvalidTransitionError,
    BrokerConfirmationRequiredError,
    TerminalStateError,
    VALID_TRANSITIONS,
)

from .transaction_log import (
    TransactionLog,
    TransactionLogError,
)

from .position_store import (
    PositionStore,
    Position,
    PositionStoreError,
)

__all__ = [
    # Order State Machine
    "OrderStateMachine",
    "OrderStatus",
    "OrderTransition",
    "OrderStateChangedEvent",
    "OrderStateMachineError",
    "InvalidTransitionError",
    "BrokerConfirmationRequiredError",
    "TerminalStateError",
    "VALID_TRANSITIONS",
    
    # Transaction Log
    "TransactionLog",
    "TransactionLogError",
    
    # Position Store
    "PositionStore",
    "Position",
    "PositionStoreError",
]
