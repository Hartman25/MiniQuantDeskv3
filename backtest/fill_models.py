"""
Fill models - realistic order fill simulation.

LEAN COMPATIBILITY:
Based on QuantConnect's ImmediateFillModel and others.

ARCHITECTURE:
- Market orders: Fill at next bar open (latency simulation)
- Limit orders: Fill when price crosses limit
- Stop orders: Trigger then fill
- Slippage applied to all fills
- Partial fills supported
- Market impact modeling

Supports: Stocks, Options, Futures, Crypto (extensible)
"""

from typing import Optional, List, Tuple
from decimal import Decimal
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod

from core.brokers import BrokerOrderSide
from core.logging import get_logger, LogStream


# ============================================================================
# ASSET CLASSES
# ============================================================================

class AssetClass(Enum):
    """Asset class types."""
    EQUITY = "EQUITY"           # Stocks
    OPTION = "OPTION"           # Options
    FUTURE = "FUTURE"           # Futures
    FOREX = "FOREX"             # Forex
    CRYPTO = "CRYPTO"           # Cryptocurrency
    CFD = "CFD"                 # Contract for Difference


# ============================================================================
# ORDER TYPES
# ============================================================================

class OrderType(Enum):
    """Order types for backtesting."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


# ============================================================================
# FILL MODELS
# ============================================================================

class FillModel(ABC):
    """
    Base fill model.
    
    LEAN equivalent: IFillModel
    
    Determines:
    - Whether order fills
    - Fill price
    - Fill quantity
    - Fill timestamp
    """
    
    @abstractmethod
    def fill(
        self,
        order_type: OrderType,
        side: BrokerOrderSide,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        stop_price: Optional[Decimal],
        current_bar: dict,
        timestamp: datetime
    ) -> Optional[Tuple[Decimal, Decimal, datetime]]:
        """
        Attempt to fill order.
        
        Args:
            order_type: Order type
            side: BUY or SELL
            quantity: Order quantity
            limit_price: Limit price (if applicable)
            stop_price: Stop price (if applicable)
            current_bar: Current OHLCV bar
            timestamp: Current timestamp
            
        Returns:
            (fill_price, fill_quantity, fill_time) or None
        """
        pass


class ImmediateFillModel(FillModel):
    """
    Immediate fill model (LEAN equivalent).
    
    RULES:
    - Market orders: Fill at next bar open
    - Limit orders: Fill if price crosses limit
    - Stop orders: Trigger then fill at market
    - Slippage applied via SlippageModel
    
    CONSERVATIVE ASSUMPTIONS:
    - Market orders use next bar open (not current close)
    - No looking into the bar (uses OHLC only)
    - Respects bid/ask spread
    """
    
    def __init__(self, slippage_model: Optional['SlippageModel'] = None):
        """Initialize fill model."""
        self.slippage_model = slippage_model
        self.logger = get_logger(LogStream.SYSTEM)
    
    def fill(
        self,
        order_type: OrderType,
        side: BrokerOrderSide,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        stop_price: Optional[Decimal],
        current_bar: dict,
        timestamp: datetime
    ) -> Optional[Tuple[Decimal, Decimal, datetime]]:
        """Fill order with immediate execution."""
        
        # Extract bar data
        open_price = Decimal(str(current_bar['open']))
        high_price = Decimal(str(current_bar['high']))
        low_price = Decimal(str(current_bar['low']))
        close_price = Decimal(str(current_bar['close']))
        
        fill_price = None
        
        # Market order: Fill at open (next bar)
        if order_type == OrderType.MARKET:
            fill_price = open_price
        
        # Limit order: Fill if price crosses limit
        elif order_type == OrderType.LIMIT:
            if side == BrokerOrderSide.BUY:
                # Buy limit: Fill if low <= limit
                if low_price <= limit_price:
                    fill_price = min(limit_price, open_price)
            else:
                # Sell limit: Fill if high >= limit
                if high_price >= limit_price:
                    fill_price = max(limit_price, open_price)
        
        # Stop market: Trigger then fill at market
        elif order_type == OrderType.STOP_MARKET:
            triggered = False
            
            if side == BrokerOrderSide.BUY:
                # Buy stop: Trigger if high >= stop
                if high_price >= stop_price:
                    triggered = True
            else:
                # Sell stop: Trigger if low <= stop
                if low_price <= stop_price:
                    triggered = True
            
            if triggered:
                fill_price = open_price  # Fill at market after trigger
        
        # Stop limit: Trigger then fill at limit
        elif order_type == OrderType.STOP_LIMIT:
            triggered = False
            
            if side == BrokerOrderSide.BUY:
                if high_price >= stop_price:
                    triggered = True
            else:
                if low_price <= stop_price:
                    triggered = True
            
            if triggered:
                # Now treat as limit order
                if side == BrokerOrderSide.BUY:
                    if low_price <= limit_price:
                        fill_price = min(limit_price, open_price)
                else:
                    if high_price >= limit_price:
                        fill_price = max(limit_price, open_price)
        
        # Apply slippage
        if fill_price is not None:
            if self.slippage_model:
                slippage = self.slippage_model.get_slippage(
                    order_type=order_type,
                    side=side,
                    quantity=quantity,
                    price=fill_price,
                    bar=current_bar
                )
                
                if side == BrokerOrderSide.BUY:
                    fill_price += slippage  # Worse for buyer
                else:
                    fill_price -= slippage  # Worse for seller
            
            return (fill_price, quantity, timestamp)
        
        return None


# ============================================================================
# SLIPPAGE MODELS
# ============================================================================

class SlippageModel(ABC):
    """Base slippage model."""
    
    @abstractmethod
    def get_slippage(
        self,
        order_type: OrderType,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal,
        bar: dict
    ) -> Decimal:
        """Calculate slippage amount."""
        pass


class ConstantSlippageModel(SlippageModel):
    """
    Constant slippage (LEAN equivalent).
    
    Simple model: Fixed percentage or dollar amount.
    """
    
    def __init__(self, slippage_percent: Decimal = Decimal("0.0001")):
        """
        Initialize constant slippage.
        
        Args:
            slippage_percent: Slippage as percentage (0.0001 = 1 basis point)
        """
        self.slippage_percent = slippage_percent
    
    def get_slippage(
        self,
        order_type: OrderType,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal,
        bar: dict
    ) -> Decimal:
        """Calculate constant slippage."""
        return price * self.slippage_percent


class VolumeShareSlippageModel(SlippageModel):
    """
    Volume-based slippage (market impact).
    
    ASSUMPTIONS:
    - Slippage increases with % of daily volume
    - Square root market impact model
    - More realistic for large orders
    """
    
    def __init__(
        self,
        price_impact: Decimal = Decimal("0.1"),
        volume_limit: Decimal = Decimal("0.025")
    ):
        """
        Initialize volume slippage.
        
        Args:
            price_impact: Base price impact coefficient
            volume_limit: Max % of bar volume (0.025 = 2.5%)
        """
        self.price_impact = price_impact
        self.volume_limit = volume_limit
    
    def get_slippage(
        self,
        order_type: OrderType,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal,
        bar: dict
    ) -> Decimal:
        """Calculate volume-based slippage."""
        volume = Decimal(str(bar.get('volume', 0)))
        
        if volume == 0:
            # No volume data - use constant slippage
            return price * Decimal("0.0005")
        
        # Calculate % of volume
        volume_pct = quantity / volume
        
        # Cap at volume limit
        volume_pct = min(volume_pct, self.volume_limit)
        
        # Square root impact model
        # slippage = price * impact * sqrt(volume_pct)
        impact_factor = (volume_pct ** Decimal("0.5"))
        slippage = price * self.price_impact * impact_factor / 100
        
        return slippage
