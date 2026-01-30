"""
Universe Management - Two-Gate System

Gate 1: Scanner produces candidates
Gate 2: Trading bot reevaluates and accepts/rejects
Universe: Core (SPY, QQQ) + accepted rolling window

Public API:
- ScannerOutputAdapter: Write candidates from scanner (Gate 1)
- UniverseInboxProcessor: Process scanner candidates (Gate 2)
- UniverseLoader: Load symbols for trading bot
- get_universe_symbols(): Convenience function
"""

from .inbox import (
    UniverseInboxProcessor,
    ScannerCandidate,
    Decision,
    UniverseState,
    CORE_UNIVERSE,
    MAX_ACCEPTED_PER_DAY,
)

from .scanner_adapter import (
    ScannerOutputAdapter,
    get_scanner_adapter,
)

from .loader import (
    UniverseLoader,
    get_universe_symbols,
    CORE_SYMBOLS,
)

__all__ = [
    "UniverseInboxProcessor",
    "ScannerOutputAdapter",
    "UniverseLoader",
    "ScannerCandidate",
    "Decision",
    "UniverseState",
    "CORE_UNIVERSE",
    "CORE_SYMBOLS",
    "MAX_ACCEPTED_PER_DAY",
    "get_scanner_adapter",
    "get_universe_symbols",
]
