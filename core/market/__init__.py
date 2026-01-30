"""
Market data and symbol management.

Provides:
- Symbol properties (tick size, lot size, constraints)
- Market hours tracking
- Security wrapper (properties + market data)
- Order validation

Pattern: LEAN Security.cs and SymbolProperties.cs

Usage:
    from core.market import SymbolPropertiesCache, SecurityCache
    
    # Initialize
    props_cache = SymbolPropertiesCache(alpaca_connector)
    security_cache = SecurityCache(props_cache)
    
    # Get security
    security = await security_cache.get_or_create('SPY')
    
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

from .symbol_properties import (
    SymbolProperties,
    SymbolPropertiesCache,
    MarketHours
)
from .security import (
    Security,
    SecurityCache,
    MarketData
)

__all__ = [
    # Symbol properties
    'SymbolProperties',
    'SymbolPropertiesCache',
    'MarketHours',
    
    # Security
    'Security',
    'SecurityCache',
    'MarketData'
]
