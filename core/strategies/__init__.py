"""Compatibility shim.

This package used to exist as core/strategies/* but the canonical runtime
strategy framework is now the top-level `strategies/*`.

Some tests (and potentially legacy modules) still import `core.strategies`.
Keep this shim tiny and explicit to avoid reintroducing the old framework.
Do NOT import this from runtime code.
"""

from __future__ import annotations

# Re-export the only thing tests still reach for.
from .performance_tracker import (  # noqa: F401
    StrategyPerformanceTracker,
    StrategyStatus,
    StrategyPerformanceSnapshot,
    StrategyPerformanceEvent,
)
