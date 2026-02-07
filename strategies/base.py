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

# ---------------------------------------------------------------------------
# PATCH 5: Purity enforcement — signal output validation
# ---------------------------------------------------------------------------

# Names of modules / class prefixes that indicate broker-level objects.
# If a strategy stores any of these as attributes, purity is violated.
_BROKER_ATTR_NAMES = frozenset({
    "broker", "_broker", "broker_connector", "_broker_connector",
    "execution_engine", "_execution_engine", "exec_engine", "_exec_engine",
})


class StrategyPurityError(TypeError):
    """Raised when a strategy violates the purity contract.

    Purity means:
      - on_bar() returns None, a dict, a StrategySignal, or a list thereof
      - the strategy does NOT hold a direct broker reference
    """


def validate_signal_output(result, *, strategy_name: str = "UNKNOWN") -> List[Dict]:
    """
    Validate and normalize the return value of ``on_bar()``.

    Accepted inputs:
      - ``None``  →  ``[]``
      - ``dict``  →  ``[dict]``
      - ``StrategySignal``  →  ``[signal.to_dict()]``
      - ``list[dict | StrategySignal]``  →  normalized list

    Anything else raises ``StrategyPurityError``.
    """
    if result is None:
        return []

    # Single typed signal
    if StrategySignal is not None and isinstance(result, StrategySignal):
        return [result.to_dict()]

    # Single dict
    if isinstance(result, dict):
        return [result]

    # List of signals
    if isinstance(result, (list, tuple)):
        out: List[Dict] = []
        for i, item in enumerate(result):
            if isinstance(item, dict):
                out.append(item)
            elif StrategySignal is not None and isinstance(item, StrategySignal):
                out.append(item.to_dict())
            else:
                raise StrategyPurityError(
                    f"Strategy '{strategy_name}' on_bar() returned a list "
                    f"containing invalid type at index {i}: {type(item).__name__}. "
                    f"Expected dict or StrategySignal."
                )
        return out

    raise StrategyPurityError(
        f"Strategy '{strategy_name}' on_bar() returned unsupported type: "
        f"{type(result).__name__}. "
        f"Must return None, dict, StrategySignal, or list thereof."
    )


def check_broker_access(strategy: "IStrategy") -> None:
    """
    Verify that a strategy does NOT hold a direct broker reference.

    Raises ``StrategyPurityError`` if a broker-like attribute is found.
    Called during ``start_strategy()`` so problems surface at init time,
    not in the middle of a live trading loop.
    """
    for attr_name in _BROKER_ATTR_NAMES:
        val = getattr(strategy, attr_name, None)
        if val is not None:
            raise StrategyPurityError(
                f"Strategy '{strategy.name}' has attribute '{attr_name}' "
                f"(type={type(val).__name__}). Strategies must NOT hold "
                f"direct broker/engine references — they return signal "
                f"intents only."
            )


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
