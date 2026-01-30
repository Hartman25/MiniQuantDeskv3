"""
VWAP Mean Reversion Strategy - Example implementation.

STRATEGY LOGIC:
1. Calculate VWAP over N bars
2. Enter LONG when price < VWAP - threshold
3. Enter SHORT when price > VWAP + threshold
4. Exit at VWAP (mean reversion)

DEMONSTRATES:
- IStrategy implementation
- MarketDataContract usage
- Signal generation
- Position tracking
- Stop/target management
"""

from decimal import Decimal
from typing import Optional, Dict, List
from collections import deque

from strategies.base import IStrategy, StrategyMetadata
from core.data.contract import MarketDataContract


# ============================================================================
# VWAP MEAN REVERSION STRATEGY
# ============================================================================

class VWAPMeanReversion(IStrategy):
    """
    Simple VWAP mean reversion strategy.
    
    CONFIG PARAMETERS:
    - vwap_period: Number of bars for VWAP calculation (default: 20)
    - entry_threshold_pct: % deviation from VWAP to enter (default: 0.01 = 1%)
    - max_positions: Maximum concurrent positions (default: 1)
    
    Example Config:
        {
            "vwap_period": 20,
            "entry_threshold_pct": 0.01,
            "max_positions": 1
        }
    """
    
    def __init__(
        self,
        name: str,
        config: Dict,
        symbols: List[str],
        timeframe: str = "1Min"
    ):
        super().__init__(name, config, symbols, timeframe)
        
        # Extract config
        self.vwap_period = config.get('vwap_period', 20)
        self.entry_threshold_pct = Decimal(str(config.get('entry_threshold_pct', 0.01)))
        self.max_positions = config.get('max_positions', 1)
        
        # Price history for VWAP calculation (symbol → deque)
        self.price_history: Dict[str, deque] = {}
        
        # VWAP values (symbol → Decimal)
        self.current_vwap: Dict[str, Decimal] = {}
    
    # ========================================================================
    # LIFECYCLE METHODS
    # ========================================================================
    
    def on_init(self) -> None:
        """Initialize strategy."""
        self.log_info(
            f"Initialized VWAP Mean Reversion "
            f"(period={self.vwap_period}, "
            f"threshold={self.entry_threshold_pct:.2%}, "
            f"max_positions={self.max_positions})"
        )
        
        # Initialize price history for each symbol
        for symbol in self.symbols:
            self.price_history[symbol] = deque(maxlen=self.vwap_period)
            self.current_vwap[symbol] = Decimal('0')
    
    def on_bar(self, bar: MarketDataContract) -> Optional[Dict]:
        """
        Process new bar.
        
        Args:
            bar: MarketDataContract with OHLCV
            
        Returns:
            Trading signal or None
        """
        symbol = bar.symbol
        
        # Update price history
        if bar.volume and bar.volume > 0:
            typical_price = (bar.high + bar.low + bar.close) / 3
            self.price_history[symbol].append({
                'price': typical_price,
                'volume': bar.volume
            })
        
        # Calculate VWAP
        vwap = self._calculate_vwap(symbol)
        if vwap is None:
            # Not enough data yet
            return None
        
        self.current_vwap[symbol] = vwap
        
        # Check if already have position
        if self.has_position(symbol):
            # Already in trade - check for exit
            return self._check_exit(symbol, bar.close, vwap)
        
        # Check if max positions reached
        if len(self.positions) >= self.max_positions:
            return None
        
        # Check entry conditions
        return self._check_entry(symbol, bar.close, vwap)
    
    def on_order_filled(
        self,
        order_id: str,
        symbol: str,
        filled_qty: Decimal,
        fill_price: Decimal
    ) -> Optional[Dict]:
        """
        Handle order fill.
        
        Args:
            order_id: Filled order ID
            symbol: Symbol
            filled_qty: Filled quantity
            fill_price: Fill price
            
        Returns:
            Optional exit signal
        """
        self.log_info(
            f"Order filled: {order_id} {filled_qty} {symbol} @ ${fill_price}"
        )
        
        # Update position tracking
        position = self.get_position(symbol)
        if position:
            self.log_info(
                f"Position opened: {symbol} "
                f"{position['side']} {position['quantity']} @ ${position['entry_price']}"
            )
        
        return None
    
    def on_stop(self) -> None:
        """Cleanup on strategy stop."""
        self.log_info(
            f"Strategy stopped "
            f"(bars={self.bars_processed}, "
            f"signals={self.signals_generated}, "
            f"fills={self.orders_filled})"
        )
    
    # ========================================================================
    # VWAP CALCULATION
    # ========================================================================
    
    def _calculate_vwap(self, symbol: str) -> Optional[Decimal]:
        """
        Calculate Volume-Weighted Average Price.
        
        Args:
            symbol: Symbol
            
        Returns:
            VWAP or None if insufficient data
        """
        history = self.price_history.get(symbol)
        if not history or len(history) < 2:
            return None
        
        total_pv = Decimal('0')  # price * volume
        total_volume = Decimal('0')
        
        for bar in history:
            pv = bar['price'] * Decimal(str(bar['volume']))
            total_pv += pv
            total_volume += Decimal(str(bar['volume']))
        
        if total_volume == 0:
            return None
        
        vwap = total_pv / total_volume
        return vwap
    
    # ========================================================================
    # ENTRY/EXIT LOGIC
    # ========================================================================
    
    def _check_entry(
        self,
        symbol: str,
        current_price: Decimal,
        vwap: Decimal
    ) -> Optional[Dict]:
        """
        Check for entry signal.
        
        Logic:
        - LONG if price < VWAP - threshold
        - SHORT if price > VWAP + threshold
        """
        threshold = vwap * self.entry_threshold_pct
        
        # LONG entry condition
        if current_price < (vwap - threshold):
            self.log_info(
                f"LONG entry signal: {symbol} "
                f"price=${current_price} < VWAP=${vwap} - threshold=${threshold}"
            )
            
            return self.create_signal(
                symbol=symbol,
                side="LONG",
                quantity=10,  # Fixed size for now (will be adjusted by sizer)
                entry_price=current_price,
                order_type="MARKET",
                take_profit=vwap,  # Exit at VWAP
                stop_loss=current_price * Decimal('0.99'),  # 1% stop
                reason=f"price_below_vwap_{self.entry_threshold_pct:.2%}"
            )
        
        # SHORT entry condition
        elif current_price > (vwap + threshold):
            self.log_info(
                f"SHORT entry signal: {symbol} "
                f"price=${current_price} > VWAP=${vwap} + threshold=${threshold}"
            )
            
            return self.create_signal(
                symbol=symbol,
                side="SHORT",
                quantity=10,
                entry_price=current_price,
                order_type="MARKET",
                take_profit=vwap,  # Exit at VWAP
                stop_loss=current_price * Decimal('1.01'),  # 1% stop
                reason=f"price_above_vwap_{self.entry_threshold_pct:.2%}"
            )
        
        return None
    
    def _check_exit(
        self,
        symbol: str,
        current_price: Decimal,
        vwap: Decimal
    ) -> Optional[Dict]:
        """
        Check for exit signal.
        
        Logic:
        - Exit LONG when price >= VWAP
        - Exit SHORT when price <= VWAP
        """
        position = self.get_position(symbol)
        if not position:
            return None
        
        side = position['side']
        
        # LONG exit
        if side == "LONG" and current_price >= vwap:
            self.log_info(
                f"LONG exit signal: {symbol} "
                f"price=${current_price} >= VWAP=${vwap}"
            )
            
            return self.create_signal(
                symbol=symbol,
                side="SHORT",  # Close LONG
                quantity=int(position['quantity']),
                entry_price=current_price,
                order_type="MARKET",
                reason="vwap_mean_reversion_target"
            )
        
        # SHORT exit
        elif side == "SHORT" and current_price <= vwap:
            self.log_info(
                f"SHORT exit signal: {symbol} "
                f"price=${current_price} <= VWAP=${vwap}"
            )
            
            return self.create_signal(
                symbol=symbol,
                side="LONG",  # Close SHORT
                quantity=int(abs(position['quantity'])),
                entry_price=current_price,
                order_type="MARKET",
                reason="vwap_mean_reversion_target"
            )
        
        return None
    
    def validate(self) -> bool:
        """Validate strategy configuration."""
        if not super().validate():
            return False
        
        if self.vwap_period < 2:
            self.log_error("vwap_period must be >= 2")
            return False
        
        if self.entry_threshold_pct <= 0:
            self.log_error("entry_threshold_pct must be positive")
            return False
        
        if self.max_positions < 1:
            self.log_error("max_positions must be >= 1")
            return False
        
        return True


# ============================================================================
# STRATEGY METADATA (Optional)
# ============================================================================

VWAP_METADATA = StrategyMetadata(
    description="Mean reversion strategy using VWAP as anchor point",
    author="MiniQuantDesk",
    version="1.0.0",
    tags=["mean-reversion", "vwap", "intraday"]
)
