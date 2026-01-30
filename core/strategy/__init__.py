"""
Strategy framework components.
"""

from .base import (
    BaseStrategy,
    TradingSignal,
    SignalType,
    SimpleMovingAverageCrossover,
)

from .portfolio import (
    PortfolioManager,
)

__all__ = [
    "BaseStrategy",
    "TradingSignal",
    "SignalType",
    "SimpleMovingAverageCrossover",
    "PortfolioManager",
]
