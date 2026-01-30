"""
Trailing stop manager for profit harvesting.

ARCHITECTURE:
- Direction-aware (LONG vs SHORT)
- Automatic profit protection
- Configurable trailing distance
- Real-time price tracking
- Stop activation thresholds

DESIGN PRINCIPLE:
Let profits run, cut losses short.

TRAILING STOP MECHANICS:
LONG Position:
  - Stop trails price UP
  - Sells when price drops X% from highest
  - Example: Entry $100, peak $110, trail 2% → Stop @ $107.80

SHORT Position:
  - Stop trails price DOWN
  - Covers when price rises X% from lowest
  - Example: Entry $100, low $90, trail 2% → Stop @ $91.80

Based on professional profit protection strategies.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# TRAILING STOP STATE
# ============================================================================

@dataclass
class TrailingStopState:
    """State of a trailing stop."""
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: Decimal
    quantity: Decimal
    
    # Trailing parameters
    trail_percent: Decimal  # How far behind to trail
    activation_profit_percent: Decimal  # Min profit before activating
    
    # Current state
    is_active: bool
    highest_price: Decimal  # For LONG
    lowest_price: Decimal   # For SHORT
    current_stop_price: Decimal
    
    # Timestamps
    entry_time: datetime
    activation_time: Optional[datetime]
    last_update_time: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": str(self.entry_price),
            "quantity": str(self.quantity),
            "trail_percent": str(self.trail_percent),
            "activation_profit_percent": str(self.activation_profit_percent),
            "is_active": self.is_active,
            "highest_price": str(self.highest_price),
            "lowest_price": str(self.lowest_price),
            "current_stop_price": str(self.current_stop_price),
            "entry_time": self.entry_time.isoformat(),
            "activation_time": self.activation_time.isoformat() if self.activation_time else None,
            "last_update_time": self.last_update_time.isoformat()
        }


class StopTrigger(Enum):
    """Stop trigger result."""
    NOT_TRIGGERED = "not_triggered"
    TRIGGERED = "triggered"
    NOT_ACTIVE = "not_active"


@dataclass
class StopCheck:
    """Result of stop check."""
    trigger: StopTrigger
    stop_price: Decimal
    current_price: Decimal
    reason: str
    
    @property
    def triggered(self) -> bool:
        """Check if stop was triggered."""
        return self.trigger == StopTrigger.TRIGGERED


# ============================================================================
# TRAILING STOP MANAGER
# ============================================================================

class TrailingStopManager:
    """
    Manage trailing stops for profit protection.
    
    RESPONSIBILITIES:
    - Track highest/lowest prices
    - Calculate trailing stop levels
    - Detect stop triggers
    - Direction-aware (LONG vs SHORT)
    - Activation thresholds
    
    TRAILING LOGIC:
    
    LONG Position:
      1. Entry: $100
      2. Price rises to $110 (+10%)
      3. If activation threshold (e.g., 5%) reached → Activate
      4. Stop trails at $110 * (1 - 0.02) = $107.80
      5. Price rises to $115 → Stop moves to $112.70
      6. Price drops to $112 → Stop TRIGGERED at $112.70
      7. Exit with profit locked
    
    SHORT Position:
      1. Entry: $100
      2. Price drops to $90 (-10%)
      3. If activation threshold reached → Activate
      4. Stop trails at $90 * (1 + 0.02) = $91.80
      5. Price drops to $85 → Stop moves to $86.70
      6. Price rises to $87 → Stop TRIGGERED at $86.70
      7. Cover with profit locked
    
    USAGE:
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        # Add position
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Update with market prices
        check = manager.update_price("AAPL", Decimal("185.00"))
        
        if check.triggered:
            logger.info(f"Stop triggered at {check.stop_price}")
            close_position("AAPL")
    """
    
    def __init__(
        self,
        default_trail_percent: Decimal = Decimal("2.0"),
        default_activation_percent: Decimal = Decimal("3.0")
    ):
        """
        Initialize trailing stop manager.
        
        Args:
            default_trail_percent: Default trailing distance (%)
            default_activation_percent: Default min profit to activate (%)
        """
        self.default_trail_percent = default_trail_percent
        self.default_activation_percent = default_activation_percent
        
        self.logger = get_logger(LogStream.RISK)
        
        # Active trailing stops
        self.stops: Dict[str, TrailingStopState] = {}
        
        # Statistics
        self.stops_triggered: List[Dict] = []
        
        self.logger.info("TrailingStopManager initialized", extra={
            "default_trail_percent": str(default_trail_percent),
            "default_activation_percent": str(default_activation_percent)
        })
    
    # ========================================================================
    # POSITION MANAGEMENT
    # ========================================================================
    
    def add_position(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        trail_percent: Optional[Decimal] = None,
        activation_profit_percent: Optional[Decimal] = None
    ):
        """
        Add a position with trailing stop.
        
        Args:
            symbol: Stock symbol
            side: "LONG" or "SHORT"
            entry_price: Entry price
            quantity: Position size
            trail_percent: Trailing distance % (default: 2%)
            activation_profit_percent: Min profit to activate (default: 3%)
        """
        trail_percent = trail_percent or self.default_trail_percent
        activation_profit_percent = activation_profit_percent or self.default_activation_percent
        
        now = datetime.now(timezone.utc)
        
        self.stops[symbol] = TrailingStopState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            trail_percent=trail_percent,
            activation_profit_percent=activation_profit_percent,
            is_active=False,
            highest_price=entry_price,  # Start at entry
            lowest_price=entry_price,   # Start at entry
            current_stop_price=Decimal("0"),  # Not active yet
            entry_time=now,
            activation_time=None,
            last_update_time=now
        )
        
        self.logger.info(f"Trailing stop added: {symbol}", extra={
            "side": side,
            "entry_price": str(entry_price),
            "trail_percent": str(trail_percent),
            "activation_percent": str(activation_profit_percent)
        })
    
    def remove_position(self, symbol: str):
        """Remove a position's trailing stop."""
        if symbol in self.stops:
            del self.stops[symbol]
            self.logger.info(f"Trailing stop removed: {symbol}")
    
    # ========================================================================
    # PRICE UPDATES
    # ========================================================================
    
    def update_price(
        self,
        symbol: str,
        current_price: Decimal
    ) -> StopCheck:
        """
        Update price and check if stop triggered.
        
        Args:
            symbol: Stock symbol
            current_price: Current market price
            
        Returns:
            StopCheck with trigger status
        """
        if symbol not in self.stops:
            return StopCheck(
                trigger=StopTrigger.NOT_ACTIVE,
                stop_price=Decimal("0"),
                current_price=current_price,
                reason="No trailing stop for this symbol"
            )
        
        stop = self.stops[symbol]
        stop.last_update_time = datetime.now(timezone.utc)
        
        # Update highest/lowest
        if stop.side == "LONG":
            if current_price > stop.highest_price:
                stop.highest_price = current_price
        else:  # SHORT
            if current_price < stop.lowest_price:
                stop.lowest_price = current_price
        
        # Check activation
        if not stop.is_active:
            profit_percent = self._calculate_profit_percent(stop, current_price)
            
            if profit_percent >= stop.activation_profit_percent:
                stop.is_active = True
                stop.activation_time = datetime.now(timezone.utc)
                
                self.logger.info(f"Trailing stop ACTIVATED: {symbol}", extra={
                    "profit_percent": str(profit_percent),
                    "activation_threshold": str(stop.activation_profit_percent)
                })
        
        # Update stop price if active
        if stop.is_active:
            stop.current_stop_price = self._calculate_stop_price(stop)
            
            # Check if triggered
            if self._is_stop_triggered(stop, current_price):
                self._log_stop_trigger(symbol, stop, current_price)
                
                return StopCheck(
                    trigger=StopTrigger.TRIGGERED,
                    stop_price=stop.current_stop_price,
                    current_price=current_price,
                    reason=f"Price crossed trailing stop ({stop.side})"
                )
        
        return StopCheck(
            trigger=StopTrigger.NOT_TRIGGERED,
            stop_price=stop.current_stop_price if stop.is_active else Decimal("0"),
            current_price=current_price,
            reason="Stop not triggered" if stop.is_active else "Stop not yet active"
        )
    
    # ========================================================================
    # CALCULATIONS
    # ========================================================================
    
    def _calculate_profit_percent(
        self,
        stop: TrailingStopState,
        current_price: Decimal
    ) -> Decimal:
        """Calculate current profit percentage."""
        if stop.side == "LONG":
            return (current_price - stop.entry_price) / stop.entry_price * Decimal("100")
        else:  # SHORT
            return (stop.entry_price - current_price) / stop.entry_price * Decimal("100")
    
    def _calculate_stop_price(self, stop: TrailingStopState) -> Decimal:
        """Calculate trailing stop price."""
        trail_multiplier = Decimal("1") - (stop.trail_percent / Decimal("100"))
        
        if stop.side == "LONG":
            # Trail below highest price
            return stop.highest_price * trail_multiplier
        else:  # SHORT
            # Trail above lowest price
            trail_multiplier = Decimal("1") + (stop.trail_percent / Decimal("100"))
            return stop.lowest_price * trail_multiplier
    
    def _is_stop_triggered(
        self,
        stop: TrailingStopState,
        current_price: Decimal
    ) -> bool:
        """Check if stop is triggered."""
        if stop.side == "LONG":
            # Sell if price drops below stop
            return current_price <= stop.current_stop_price
        else:  # SHORT
            # Cover if price rises above stop
            return current_price >= stop.current_stop_price
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def _log_stop_trigger(
        self,
        symbol: str,
        stop: TrailingStopState,
        trigger_price: Decimal
    ):
        """Log a stop trigger event."""
        profit_percent = self._calculate_profit_percent(stop, trigger_price)
        
        self.logger.warning(f"Trailing stop TRIGGERED: {symbol}", extra={
            "side": stop.side,
            "entry_price": str(stop.entry_price),
            "trigger_price": str(trigger_price),
            "stop_price": str(stop.current_stop_price),
            "profit_percent": str(profit_percent)
        })
        
        self.stops_triggered.append({
            "timestamp": datetime.now(timezone.utc),
            "symbol": symbol,
            "side": stop.side,
            "entry_price": stop.entry_price,
            "trigger_price": trigger_price,
            "stop_price": stop.current_stop_price,
            "profit_percent": profit_percent
        })
    
    def get_stop_count(self) -> int:
        """Get count of active trailing stops."""
        return len(self.stops)
    
    def get_active_stop(self, symbol: str) -> Optional[TrailingStopState]:
        """Get active trailing stop for a symbol."""
        return self.stops.get(symbol)
    
    def get_all_stops(self) -> Dict[str, TrailingStopState]:
        """Get all active trailing stops."""
        return self.stops.copy()
    
    def get_statistics(self) -> Dict:
        """Get trailing stop statistics."""
        return {
            "active_stops": len(self.stops),
            "stops_triggered": len(self.stops_triggered),
            "config": {
                "default_trail_percent": str(self.default_trail_percent),
                "default_activation_percent": str(self.default_activation_percent)
            },
            "current_stops": {
                symbol: stop.to_dict()
                for symbol, stop in self.stops.items()
            }
        }
    
    # ========================================================================
    # BATCH UPDATES
    # ========================================================================
    
    def update_all_prices(
        self,
        prices: Dict[str, Decimal]
    ) -> Dict[str, StopCheck]:
        """
        Update prices for all positions at once.
        
        Args:
            prices: Dictionary of symbol -> current_price
            
        Returns:
            Dictionary of symbol -> StopCheck
        """
        results = {}
        
        for symbol in self.stops.keys():
            if symbol in prices:
                results[symbol] = self.update_price(symbol, prices[symbol])
        
        return results
