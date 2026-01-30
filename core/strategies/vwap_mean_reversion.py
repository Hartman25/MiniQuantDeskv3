"""
VWAP mean reversion strategy.
"""

from typing import Optional
from decimal import Decimal
from datetime import datetime

import pandas as pd
import numpy as np

from core.strategies.base import BaseStrategy, Signal, SignalDirection


class VWAPMeanReversionStrategy(BaseStrategy):
    """
    VWAP mean reversion.
    
    LOGIC:
    - Buy when price < VWAP - threshold
    - Sell when price > VWAP
    """
    
    def __init__(self, name: str, symbols: list, params: dict, clock):
        params.setdefault("vwap_period", 20)
        params.setdefault("std_multiplier", 2.0)
        params.setdefault("min_confidence", 0.6)
        super().__init__(name, symbols, params, clock)
    
    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Optional[Signal]:
        """Generate signal."""
        if len(bars) < self.params["vwap_period"]:
            return None
        
        vwap = self._calc_vwap(bars, self.params["vwap_period"])
        std = self._calc_std(bars, vwap, self.params["vwap_period"])
        
        current = Decimal(str(bars.iloc[-1]['close']))
        lower = vwap - (std * Decimal(str(self.params["std_multiplier"])))
        
        # LONG entry
        if current < lower:
            distance = abs(current - vwap) / std if std > 0 else Decimal("0")
            confidence = min(Decimal("1.0"), distance / Decimal("3.0"))
            
            if confidence >= Decimal(str(self.params["min_confidence"])):
                return Signal(
                    strategy_name=self.name,
                    symbol=symbol,
                    direction=SignalDirection.LONG,
                    confidence=confidence,
                    timestamp=self.clock.now(),  # Use injected clock
                    entry_price=current,
                    stop_loss=current * Decimal("0.98"),
                    take_profit=vwap,
                    metadata={"vwap": str(vwap), "std": str(std)}
                )
        
        # LONG exit
        elif current > vwap:
            return Signal(
                strategy_name=self.name,
                symbol=symbol,
                direction=SignalDirection.CLOSE_LONG,
                confidence=Decimal("0.8"),
                timestamp=self.clock.now(),  # Use injected clock
                metadata={"vwap": str(vwap)}
            )
        
        return None
    
    def _calc_vwap(self, bars: pd.DataFrame, period: int) -> Decimal:
        """Calculate VWAP."""
        recent = bars.tail(period)
        typical = (recent['high'] + recent['low'] + recent['close']) / 3
        vwap = (typical * recent['volume']).sum() / recent['volume'].sum()
        return Decimal(str(vwap))
    
    def _calc_std(self, bars: pd.DataFrame, vwap: Decimal, period: int) -> Decimal:
        """Calculate std from VWAP."""
        recent = bars.tail(period)
        prices = (recent['high'] + recent['low'] + recent['close']) / 3
        deviations = prices - float(vwap)
        std = deviations.std()
        return Decimal(str(std)) if not np.isnan(std) else Decimal("0")
