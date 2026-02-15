"""
Order execution engine.

PATCH 4 (2026-02-14): Removed deprecated reconciliation module.
Active reconciler is now at: core.state.reconciler
"""

from .engine import (
    OrderExecutionEngine,
    OrderExecutionError,
)

__all__ = [
    "OrderExecutionEngine",
    "OrderExecutionError",
]
