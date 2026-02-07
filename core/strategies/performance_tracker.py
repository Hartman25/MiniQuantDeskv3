"""Compatibility shim for `core.strategies.performance_tracker`.

This repo's canonical strategy framework is `strategies/*`.

Some tests (and older modules) still import `core.strategies.performance_tracker`.
To avoid keeping two full strategy frameworks alive, this file re-exports the
*performance tracker* types from whichever implementation is available.

Rules:
- Runtime code should NOT import `core.strategies.*` (canonical is `strategies/*`).
- This shim is intentionally tiny and explicit.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Optional

# Try canonical first, then legacy fallbacks.
_CANDIDATES = (
    # If you add a canonical implementation later, this will win.
    "strategies.performance_tracker",
    # Some prior refactors colocated it here (keep but validate symbols).
    "strategies.retirement",
    # Legacy location after moving `core/strategies` -> `core/_legacy/core_strategies`.
    "core._legacy.core_strategies.strategies.performance_tracker",
    # Extra safety (if layout differs slightly).
    "core._legacy.core_strategies.performance_tracker",
)

_REQUIRED = ("StrategyPerformanceTracker",)

def _load() -> ModuleType:
    last_err: Optional[BaseException] = None
    for modname in _CANDIDATES:
        try:
            mod = import_module(modname)
        except Exception as e:  # pragma: no cover
            last_err = e
            continue

        # Validate required symbols exist before accepting the module.
        if all(hasattr(mod, sym) for sym in _REQUIRED):
            return mod

        # Module imported but doesn't have what we need; keep searching.
        last_err = AttributeError(
            f"Module '{modname}' missing required symbols: "
            + ", ".join(sym for sym in _REQUIRED if not hasattr(mod, sym))
        )

    raise ImportError(
        "Could not locate StrategyPerformanceTracker implementation. Tried: "
        + ", ".join(_CANDIDATES)
    ) from last_err

_mod = _load()

StrategyPerformanceTracker = getattr(_mod, "StrategyPerformanceTracker")
StrategyStatus = getattr(_mod, "StrategyStatus", None)
StrategyPerformanceSnapshot = getattr(_mod, "StrategyPerformanceSnapshot", None)
StrategyPerformanceEvent = getattr(_mod, "StrategyPerformanceEvent", None)

__all__ = [
    "StrategyPerformanceTracker",
    "StrategyStatus",
    "StrategyPerformanceSnapshot",
    "StrategyPerformanceEvent",
]
