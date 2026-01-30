"""
Security - Complete symbol representation.

Combines:
- Symbol properties (tick size, lot size, etc)
- Current market data (price, volume)
- Position information

Pattern stolen from: LEAN Security.cs
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from datetime import datetime
import logging

from .symbol_properties import SymbolProperties

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """Current market data for symbol"""
    symbol: str
    last_price: Optional[Decimal] = None
    bid_price: Optional[Decimal] = None
    ask_price: Optional[Decimal] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    volume: Optional[int] = None
    timestamp: Optional[datetime] = None
    
    @property
    def spread(self) -> Optional[Decimal]:
        """Bid-ask spread"""
        if self.bid_price and self.ask_price:
            return self.ask_price - self.bid_price
        return None
    
    @property
    def mid_price(self) -> Optional[Decimal]:
        """Mid price"""
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / Decimal('2')
        return None


class Security:
    """
    Complete security representation.
    
    Combines symbol properties with current market data.
    Main interface for working with symbols.
    
    Usage:
        security = Security(
            symbol='SPY',
            properties=symbol_props
        )
        
        # Update market data
        security.update_market_data(MarketData(...))
        
        # Validate order
        is_valid, reason = security.validate_order(
            quantity=10,
            price=Decimal('450.13'),
            side='BUY'
        )
        
        # Round values
        price = security.round_price(Decimal('450.127'))
        qty = security.round_quantity(15)
    """
    
    def __init__(
        self,
        symbol: str,
        properties: SymbolProperties
    ):
        """
        Args:
            symbol: Symbol ticker
            properties: Symbol properties
        """
        self.symbol = symbol
        self.properties = properties
        self.market_data: Optional[MarketData] = None
        
        logger.debug(f"Security created: {symbol}")
    
    def update_market_data(self, data: MarketData):
        """Update current market data"""
        self.market_data = data
    
    def validate_order(
        self,
        quantity: int,
        price: Optional[Decimal],
        side: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order against symbol properties.
        
        Delegates to SymbolProperties.
        """
        return self.properties.validate_order(quantity, price, side)
    
    def round_price(self, price: Decimal) -> Decimal:
        """Round price to valid increment"""
        return self.properties.round_price(price)
    
    def round_quantity(self, quantity: int) -> int:
        """Round quantity to valid lot size"""
        return self.properties.round_quantity(quantity)
    
    def calculate_margin_requirement(
        self,
        quantity: int,
        price: Decimal
    ) -> Decimal:
        """Calculate margin requirement"""
        return self.properties.calculate_margin_requirement(quantity, price)
    
    @property
    def current_price(self) -> Optional[Decimal]:
        """Get current price (prefer last, fallback to mid)"""
        if not self.market_data:
            return None
        
        if self.market_data.last_price:
            return self.market_data.last_price
        
        return self.market_data.mid_price
    
    @property
    def is_tradable(self) -> bool:
        """Check if currently tradable"""
        return self.properties.is_tradable
    
    @property
    def is_shortable(self) -> bool:
        """Check if shortable"""
        return self.properties.is_shortable
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        result = {
            'symbol': self.symbol,
            'properties': self.properties.to_dict(),
        }
        
        if self.market_data:
            result['market_data'] = {
                'last_price': str(self.market_data.last_price) if self.market_data.last_price else None,
                'bid': str(self.market_data.bid_price) if self.market_data.bid_price else None,
                'ask': str(self.market_data.ask_price) if self.market_data.ask_price else None,
                'spread': str(self.market_data.spread) if self.market_data.spread else None,
                'volume': self.market_data.volume,
                'timestamp': self.market_data.timestamp.isoformat() if self.market_data.timestamp else None
            }
        
        return result


class SecurityCache:
    """
    Cache of Security objects.
    
    Manages Security instances with their properties and market data.
    
    Usage:
        cache = SecurityCache(symbol_props_cache)
        
        # Get or create security
        security = await cache.get_or_create('SPY')
        
        # Update market data
        cache.update_market_data('SPY', MarketData(...))
    """
    
    def __init__(self, symbol_properties_cache):
        """
        Args:
            symbol_properties_cache: SymbolPropertiesCache instance
        """
        self._props_cache = symbol_properties_cache
        self._cache: dict[str, Security] = {}
        
        logger.info("SecurityCache initialized")
    
    async def get_or_create(self, symbol: str) -> Security:
        """
        Get or create Security instance.
        
        Args:
            symbol: Symbol ticker
            
        Returns:
            Security instance
        """
        if symbol in self._cache:
            return self._cache[symbol]
        
        # Load properties
        props = await self._props_cache.load_symbol(symbol)
        
        # Create security
        security = Security(symbol, props)
        self._cache[symbol] = security
        
        logger.debug(f"Created security: {symbol}")
        
        return security
    
    def get(self, symbol: str) -> Optional[Security]:
        """Get cached Security"""
        return self._cache.get(symbol)
    
    def update_market_data(self, symbol: str, data: MarketData):
        """Update market data for security"""
        security = self._cache.get(symbol)
        
        if security:
            security.update_market_data(data)
        else:
            logger.warning(f"Tried to update market data for unknown security: {symbol}")
    
    def clear(self):
        """Clear cache"""
        self._cache.clear()
        logger.info("Security cache cleared")
    
    def get_stats(self) -> dict:
        """Get cache statistics"""
        return {
            'cached_securities': len(self._cache),
            'symbols': list(self._cache.keys())
        }
