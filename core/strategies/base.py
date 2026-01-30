"""
Strategy base class and signal generation.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from core.logging import get_logger, LogStream
from core.time import Clock


class SignalDirection(Enum):
    """Signal direction."""
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"


@dataclass
class Signal:
    """Trading signal."""
    strategy_name: str
    symbol: str
    direction: SignalDirection
    confidence: Decimal
    timestamp: datetime
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"Confidence must be 0-1, got {self.confidence}")


class BaseStrategy(ABC):
    """
    Base strategy class.
    
    Subclasses implement on_bar() to generate signals.
    """
    
    def __init__(self, name: str, symbols: List[str], params: Dict, clock: Clock):
        self.name = name
        self.symbols = symbols
        self.params = params
        self.clock = clock  # NEW: Injectable clock for backtesting
        self.logger = get_logger(LogStream.TRADING)
        
        self.logger.info(f"Strategy initialized: {name}")
    
    @abstractmethod
    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Optional[Signal]:
        """Generate signal from bars."""
        raise NotImplementedError()
    
    def on_position_opened(self, symbol: str, entry_price: Decimal, quantity: Decimal):
        """Called when position opened."""
        self.logger.info(f"Position opened: {symbol} @ ${entry_price}")
    
    def on_position_closed(
        self,
        symbol: str,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        pnl: Decimal
    ):
        """Called when position closed."""
        pnl_pct = pnl / (entry_price * quantity) * 100
        self.logger.info(f"Position closed: {symbol}, PnL: ${pnl} ({pnl_pct:.2f}%)")
