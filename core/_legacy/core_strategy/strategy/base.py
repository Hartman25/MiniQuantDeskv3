"""
Strategy framework - base classes and signal types.

ARCHITECTURE:
- Base strategy class with lifecycle hooks
- Signal generation (LONG/SHORT/FLAT)
- Strategy state management
- Performance tracking
- Multi-timeframe support

Based on QuantConnect's Algorithm pattern.
"""

from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod

import pandas as pd

from core.logging import get_logger, LogStream


# ============================================================================
# SIGNAL TYPES
# ============================================================================

class SignalType(Enum):
    """Trading signal type."""
    LONG = "LONG"    # Buy signal
    SHORT = "SHORT"  # Sell/short signal
    FLAT = "FLAT"    # Close position
    HOLD = "HOLD"    # Do nothing


@dataclass
class TradingSignal:
    """Trading signal from strategy."""
    symbol: str
    signal_type: SignalType
    strength: Decimal  # 0.0 to 1.0
    timestamp: datetime
    strategy: str
    metadata: Dict
    
    # Optional stop/target
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None


# ============================================================================
# BASE STRATEGY
# ============================================================================

class BaseStrategy(ABC):
    """
    Base strategy class.
    
    LIFECYCLE:
    1. initialize() - Setup indicators, parameters
    2. on_data(bars) - Process new data
    3. generate_signal() - Generate trading signal
    4. on_fill(order) - Handle order fills
    
    USAGE:
        class MyStrategy(BaseStrategy):
            def initialize(self):
                self.period = 20
                
            def on_data(self, bars):
                # Update indicators
                pass
                
            def generate_signal(self, symbol):
                # Generate signal
                return TradingSignal(...)
    """
    
    def __init__(self, name: str, symbols: List[str], parameters: Optional[Dict] = None):
        """Initialize strategy."""
        self.name = name
        self.symbols = symbols
        self.parameters = parameters or {}
        self.logger = get_logger(LogStream.STRATEGY)
        
        # State
        self.is_initialized = False
        self.last_update = None
        
        # Performance
        self.signals_generated = 0
        self.trades_executed = 0
        
        self.logger.info(f"Strategy created: {name}", extra={
            "strategy": name,
            "symbols": symbols,
            "parameters": parameters
        })
    
    @abstractmethod
    def initialize(self):
        """Initialize strategy (indicators, state, etc)."""
        pass
    
    @abstractmethod
    def on_data(self, bars: pd.DataFrame):
        """
        Process new market data.
        
        Args:
            bars: DataFrame with OHLCV data
        """
        pass
    
    @abstractmethod
    def generate_signal(self, symbol: str) -> Optional[TradingSignal]:
        """
        Generate trading signal for symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            TradingSignal or None
        """
        pass
    
    def on_fill(self, order_id: str, symbol: str, quantity: Decimal, price: Decimal):
        """
        Handle order fill.
        
        Args:
            order_id: Order ID
            symbol: Symbol
            quantity: Fill quantity
            price: Fill price
        """
        self.trades_executed += 1
        self.logger.info(f"Trade executed: {symbol}", extra={
            "strategy": self.name,
            "order_id": order_id,
            "symbol": symbol,
            "quantity": str(quantity),
            "price": str(price)
        })
    
    def get_state(self) -> Dict:
        """Get strategy state."""
        return {
            "name": self.name,
            "symbols": self.symbols,
            "parameters": self.parameters,
            "is_initialized": self.is_initialized,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "signals_generated": self.signals_generated,
            "trades_executed": self.trades_executed
        }


# ============================================================================
# SIMPLE STRATEGY EXAMPLE
# ============================================================================

class SimpleMovingAverageCrossover(BaseStrategy):
    """
    Simple MA crossover strategy (example).
    
    BUY: Fast MA crosses above slow MA
    SELL: Fast MA crosses below slow MA
    """
    
    def __init__(self, symbols: List[str], fast_period: int = 10, slow_period: int = 20):
        super().__init__(
            name="MA_Crossover",
            symbols=symbols,
            parameters={"fast_period": fast_period, "slow_period": slow_period}
        )
        self.fast_period = fast_period
        self.slow_period = slow_period
        
        # State
        self.fast_ma: Dict[str, Optional[Decimal]] = {s: None for s in symbols}
        self.slow_ma: Dict[str, Optional[Decimal]] = {s: None for s in symbols}
        self.prev_fast_ma: Dict[str, Optional[Decimal]] = {s: None for s in symbols}
        self.prev_slow_ma: Dict[str, Optional[Decimal]] = {s: None for s in symbols}
    
    def initialize(self):
        """Initialize strategy."""
        self.is_initialized = True
        self.logger.info("MA Crossover strategy initialized", extra={
            "fast_period": self.fast_period,
            "slow_period": self.slow_period
        })
    
    def on_data(self, bars: pd.DataFrame):
        """Update moving averages."""
        if len(bars) < self.slow_period:
            return  # Not enough data
        
        for symbol in self.symbols:
            # Store previous values
            self.prev_fast_ma[symbol] = self.fast_ma[symbol]
            self.prev_slow_ma[symbol] = self.slow_ma[symbol]
            
            # Calculate MAs
            closes = bars['close'].tail(self.slow_period)
            self.fast_ma[symbol] = Decimal(str(closes.tail(self.fast_period).mean()))
            self.slow_ma[symbol] = Decimal(str(closes.mean()))
        
        self.last_update = datetime.now()
    
    def generate_signal(self, symbol: str) -> Optional[TradingSignal]:
        """Generate crossover signal."""
        fast = self.fast_ma.get(symbol)
        slow = self.slow_ma.get(symbol)
        prev_fast = self.prev_fast_ma.get(symbol)
        prev_slow = self.prev_slow_ma.get(symbol)
        
        if not all([fast, slow, prev_fast, prev_slow]):
            return None  # Not enough data
        
        # Check for crossover
        signal_type = SignalType.HOLD
        strength = Decimal("0")
        
        # Bullish crossover
        if prev_fast <= prev_slow and fast > slow:
            signal_type = SignalType.LONG
            strength = Decimal("0.8")
        
        # Bearish crossover
        elif prev_fast >= prev_slow and fast < slow:
            signal_type = SignalType.SHORT
            strength = Decimal("0.8")
        
        if signal_type != SignalType.HOLD:
            self.signals_generated += 1
            
            return TradingSignal(
                symbol=symbol,
                signal_type=signal_type,
                strength=strength,
                timestamp=datetime.now(),
                strategy=self.name,
                metadata={
                    "fast_ma": str(fast),
                    "slow_ma": str(slow)
                }
            )
        
        return None
