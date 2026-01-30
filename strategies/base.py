"""
IStrategy - Abstract Base Class for all trading strategies.

CRITICAL CONTRACT:
1. All strategies MUST inherit from IStrategy
2. All strategies MUST implement on_bar()
3. All strategies receive MarketDataContract (not provider-specific types)
4. Strategies MAY emit typed StrategySignal or legacy dict signals
5. Strategy output is INTENT, not direct broker orders

This ensures consistent strategy behavior and hot-swappability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, List, Union
from decimal import Decimal
from dataclasses import dataclass
import logging

from core.data.contract import MarketDataContract
from core.time import Clock


@dataclass
class StrategyMetadata:
    """Optional metadata for strategies."""
    description: str = ""
    author: str = ""
    version: str = "1.0.0"
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []

try:
    # Optional: new typed signal object
    from strategies.signals import StrategySignal
except Exception:  # pragma: no cover
    StrategySignal = None  # type: ignore


SignalLike = Union[Dict, "StrategySignal"] if StrategySignal else Dict

logger = logging.getLogger(__name__)


class IStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    """

    # Recommended: override in strategy (warmup bars needed before signals)
    warmup_bars: int = 0

    def __init__(self, name: str, config: Dict, symbols: List[str], timeframe: str = "1Min", clock: Optional[Clock] = None):
        self.name = name
        self.config = config
        self.symbols = [s.upper() for s in symbols]
        self.timeframe = timeframe
        self.clock = clock  # NEW: Injectable clock for backtesting (optional for compatibility)

        # Strategy lifecycle state
        self.enabled: bool = False

        # Basic stats (optional)
        self.bars_processed: int = 0
        self.signals_generated: int = 0
        self.orders_filled: int = 0

        # Logger
        self.logger = logging.getLogger(f"strategy.{name}")

    @abstractmethod
    def on_init(self) -> None:
        """Called once on startup."""
        raise NotImplementedError

    @abstractmethod
    def on_bar(self, bar: MarketDataContract) -> Optional[SignalLike]:
        """
        Called for each new COMPLETE bar.
        Return:
          - StrategySignal (typed) OR
          - dict (legacy) OR
          - None
        """
        raise NotImplementedError

    def on_order_filled(self, order_id: str, symbol: str, filled_qty: Decimal, fill_price: Decimal) -> Optional[SignalLike]:
        """Called when an order is filled."""
        return None

    def on_order_rejected(self, order_id: str, symbol: str, reason: str) -> Optional[SignalLike]:
        """Called when an order is rejected."""
        return None

    def on_stop(self) -> None:
        """Called on strategy shutdown."""
        return None
    
    def validate(self) -> bool:
        """
        Validate strategy configuration.
        
        Override in subclass for custom validation.
        Default: always valid.
        """
        return True

    # Convenience logging helpers
    def log_info(self, msg: str, **kwargs):
        self.logger.info(msg, extra=kwargs)

    def log_warning(self, msg: str, **kwargs):
        self.logger.warning(msg, extra=kwargs)

    def log_error(self, msg: str, **kwargs):
        self.logger.error(msg, extra=kwargs)
