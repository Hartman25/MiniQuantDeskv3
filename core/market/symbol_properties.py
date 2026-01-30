"""
Symbol Properties - Metadata for trading symbols.

Stores critical information about each symbol:
- Minimum price increment (tick size)
- Minimum order quantity (lot size)
- Market hours (trading windows)
- Margin requirements
- Shortability

Prevents illegal orders and rounding errors.

Pattern stolen from: LEAN Security.cs and SymbolProperties.cs
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from datetime import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class MarketHours:
    """Trading hours for a symbol"""
    market_open: time = time(9, 30)   # 9:30 AM ET
    market_close: time = time(16, 0)  # 4:00 PM ET
    pre_market_open: Optional[time] = time(4, 0)   # 4:00 AM ET
    after_hours_close: Optional[time] = time(20, 0)  # 8:00 PM ET
    
    def is_regular_hours(self, current_time: time) -> bool:
        """Check if time is in regular trading hours"""
        return self.market_open <= current_time < self.market_close
    
    def is_extended_hours(self, current_time: time) -> bool:
        """Check if time is in extended hours"""
        if self.pre_market_open and current_time >= self.pre_market_open:
            if current_time < self.market_open:
                return True  # Pre-market
        
        if self.after_hours_close and current_time >= self.market_close:
            if current_time < self.after_hours_close:
                return True  # After-hours
        
        return False
    
    def is_market_open(self, current_time: time) -> bool:
        """Check if market is open (regular or extended)"""
        return self.is_regular_hours(current_time) or self.is_extended_hours(current_time)


@dataclass
class SymbolProperties:
    """
    Complete properties for a trading symbol.
    
    Critical for order validation:
    - Prevents submitting orders with wrong price increments
    - Prevents submitting orders with wrong lot sizes
    - Prevents trading during closed hours
    - Prevents shorting non-shortable stocks
    
    Example:
        props = SymbolProperties(
            symbol='SPY',
            min_price_increment=Decimal('0.01'),
            lot_size=1,
            min_order_size=1,
            is_shortable=True
        )
        
        # Round price correctly
        price = props.round_price(Decimal('450.127'))  # -> 450.13
        
        # Validate order
        is_valid, reason = props.validate_order(
            quantity=10,
            price=Decimal('450.13'),
            side='BUY'
        )
    """
    
    # Identity
    symbol: str
    
    # Price constraints
    min_price_increment: Decimal = Decimal('0.01')  # Tick size
    
    # Quantity constraints
    lot_size: int = 1  # Minimum quantity increment
    min_order_size: int = 1  # Minimum order quantity
    max_order_size: Optional[int] = None  # Maximum order quantity
    
    # Trading constraints
    is_tradable: bool = True
    is_shortable: bool = True
    is_fractionable: bool = False  # Can trade fractional shares
    
    # Margin/leverage
    margin_requirement: Decimal = Decimal('2.0')  # 50% margin = 2x leverage
    maintenance_margin_requirement: Decimal = Decimal('4.0')  # 25% maintenance
    
    # Market hours
    market_hours: MarketHours = None
    
    # Asset class
    asset_class: str = "us_equity"  # us_equity, crypto, etc
    
    # Exchange
    exchange: str = "NASDAQ"
    
    def __post_init__(self):
        if self.market_hours is None:
            self.market_hours = MarketHours()
    
    def round_price(self, price: Decimal) -> Decimal:
        """
        Round price to valid increment.
        
        Args:
            price: Raw price
            
        Returns:
            Price rounded to min_price_increment
            
        Example:
            # For SPY with 0.01 tick size:
            round_price(450.127) -> 450.13
            round_price(450.124) -> 450.12
        """
        if self.min_price_increment == 0:
            return price
        
        # Round to nearest increment
        increment = self.min_price_increment
        rounded = (price / increment).quantize(Decimal('1'))
        result = rounded * increment
        
        return result
    
    def round_quantity(self, quantity: int) -> int:
        """
        Round quantity to valid lot size.
        
        Args:
            quantity: Raw quantity
            
        Returns:
            Quantity rounded to lot_size
            
        Example:
            # For symbol with lot_size=100:
            round_quantity(250) -> 200
            round_quantity(150) -> 100
        """
        if self.lot_size <= 1:
            return quantity
        
        # Round down to nearest lot
        lots = quantity // self.lot_size
        result = lots * self.lot_size
        
        return max(result, 0)
    
    def validate_order(
        self,
        quantity: int,
        price: Optional[Decimal],
        side: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order against symbol properties.
        
        Args:
            quantity: Order quantity
            price: Order price (None for market orders)
            side: 'BUY' or 'SELL'
            
        Returns:
            (is_valid, reason) tuple
        """
        # Check tradability
        if not self.is_tradable:
            return False, f"{self.symbol} is not tradable"
        
        # Check shortability
        if side == 'SELL' and not self.is_shortable:
            return False, f"{self.symbol} is not shortable"
        
        # Check quantity constraints
        if quantity < self.min_order_size:
            return False, f"Quantity {quantity} below minimum {self.min_order_size}"
        
        if self.max_order_size and quantity > self.max_order_size:
            return False, f"Quantity {quantity} exceeds maximum {self.max_order_size}"
        
        # Check lot size
        if quantity % self.lot_size != 0:
            return False, f"Quantity {quantity} not multiple of lot size {self.lot_size}"
        
        # Check price increment (for limit orders)
        if price is not None:
            rounded_price = self.round_price(price)
            if rounded_price != price:
                return False, f"Price {price} not valid increment (should be {rounded_price})"
        
        return True, None
    
    def calculate_margin_requirement(self, quantity: int, price: Decimal) -> Decimal:
        """
        Calculate margin requirement for position.
        
        Args:
            quantity: Position quantity
            price: Entry price
            
        Returns:
            Margin requirement in dollars
        """
        notional_value = quantity * price
        margin_required = notional_value / self.margin_requirement
        return margin_required
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'min_price_increment': str(self.min_price_increment),
            'lot_size': self.lot_size,
            'min_order_size': self.min_order_size,
            'max_order_size': self.max_order_size,
            'is_tradable': self.is_tradable,
            'is_shortable': self.is_shortable,
            'is_fractionable': self.is_fractionable,
            'margin_requirement': str(self.margin_requirement),
            'maintenance_margin_requirement': str(self.maintenance_margin_requirement),
            'asset_class': self.asset_class,
            'exchange': self.exchange
        }
    
    @staticmethod
    def from_alpaca_asset(asset: dict) -> 'SymbolProperties':
        """
        Create SymbolProperties from Alpaca asset data.
        
        Args:
            asset: Asset dict from Alpaca API
            
        Returns:
            SymbolProperties instance
        """
        return SymbolProperties(
            symbol=asset['symbol'],
            min_price_increment=Decimal('0.01'),  # Standard for US equities
            lot_size=1,
            min_order_size=1,
            is_tradable=asset.get('tradable', True),
            is_shortable=asset.get('shortable', False),
            is_fractionable=asset.get('fractionable', False),
            margin_requirement=Decimal('2.0'),  # Standard 50% margin
            asset_class=asset.get('class', 'us_equity'),
            exchange=asset.get('exchange', 'UNKNOWN')
        )


class SymbolPropertiesCache:
    """
    Cache for symbol properties.
    
    Loads from Alpaca API and caches in memory.
    
    Usage:
        cache = SymbolPropertiesCache(alpaca_connector)
        
        # Load properties
        await cache.load_symbol('SPY')
        
        # Get properties
        props = cache.get('SPY')
        
        # Validate order
        is_valid, reason = cache.validate_order(
            symbol='SPY',
            quantity=10,
            price=Decimal('450.13'),
            side='BUY'
        )
    """
    
    def __init__(self, alpaca_connector):
        """
        Args:
            alpaca_connector: Alpaca broker connector
        """
        self._connector = alpaca_connector
        self._cache: dict[str, SymbolProperties] = {}
        
        logger.info("SymbolPropertiesCache initialized")
    
    async def load_symbol(self, symbol: str) -> SymbolProperties:
        """
        Load symbol properties from Alpaca.
        
        Args:
            symbol: Symbol to load
            
        Returns:
            SymbolProperties instance
        """
        if symbol in self._cache:
            return self._cache[symbol]
        
        try:
            # Get asset from Alpaca
            asset = await self._connector.get_asset(symbol)
            
            # Create properties
            props = SymbolProperties.from_alpaca_asset(asset)
            
            # Cache
            self._cache[symbol] = props
            
            logger.info(
                f"Loaded symbol properties: {symbol}",
                extra={
                    'symbol': symbol,
                    'tradable': props.is_tradable,
                    'shortable': props.is_shortable,
                    'exchange': props.exchange
                }
            )
            
            return props
            
        except Exception as e:
            logger.error(
                f"Failed to load symbol properties: {symbol}",
                extra={'error': str(e)},
                exc_info=True
            )
            
            # Return default properties
            default_props = SymbolProperties(symbol=symbol)
            self._cache[symbol] = default_props
            return default_props
    
    async def load_multiple(self, symbols: list[str]):
        """Load multiple symbols"""
        for symbol in symbols:
            await self.load_symbol(symbol)
    
    def get(self, symbol: str) -> Optional[SymbolProperties]:
        """Get cached properties"""
        return self._cache.get(symbol)
    
    def validate_order(
        self,
        symbol: str,
        quantity: int,
        price: Optional[Decimal],
        side: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order using cached properties.
        
        Returns:
            (is_valid, reason) tuple
        """
        props = self.get(symbol)
        
        if not props:
            return False, f"Symbol properties not loaded for {symbol}"
        
        return props.validate_order(quantity, price, side)
    
    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """Round price for symbol"""
        props = self.get(symbol)
        
        if not props:
            logger.warning(f"Symbol properties not loaded for {symbol}, using raw price")
            return price
        
        return props.round_price(price)
    
    def round_quantity(self, symbol: str, quantity: int) -> int:
        """Round quantity for symbol"""
        props = self.get(symbol)
        
        if not props:
            logger.warning(f"Symbol properties not loaded for {symbol}, using raw quantity")
            return quantity
        
        return props.round_quantity(quantity)
    
    def clear(self):
        """Clear cache"""
        self._cache.clear()
        logger.info("Symbol properties cache cleared")
    
    def get_stats(self) -> dict:
        """Get cache statistics"""
        return {
            'cached_symbols': len(self._cache),
            'symbols': list(self._cache.keys())
        }
