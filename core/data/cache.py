"""
Unified Data Cache - Reduces redundant API calls.

RULES:
1. Cache MarketDataContract objects (not provider-specific types)
2. Respect staleness (auto-expire old data)
3. Thread-safe access
4. LRU eviction when full
5. Cache key = (symbol, timeframe, timestamp)

Prevents rate limit issues with data providers.
"""

from typing import Optional, Dict, Tuple
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from threading import Lock
from collections import OrderedDict
import logging

from core.data.contract import MarketDataContract

logger = logging.getLogger(__name__)


# ============================================================================
# DATA CACHE
# ============================================================================

class DataCache:
    """
    LRU cache for market data with staleness tracking.
    
    Usage:
        cache = DataCache(max_size=1000, max_age_seconds=300)
        
        # Store bar
        cache.put(bar)
        
        # Retrieve bar
        bar = cache.get("SPY", "1Min", timestamp)
        
        # Check if fresh
        if bar and not bar.is_stale(90):
            # Use cached data
            pass
    """
    
    def __init__(
        self,
        max_size: int = 10000,
        max_age_seconds: int = 300
    ):
        """
        Initialize cache.
        
        Args:
            max_size: Maximum cached items (LRU eviction)
            max_age_seconds: Maximum age before auto-expiry
        """
        self.max_size = max_size
        self.max_age_seconds = max_age_seconds
        
        self._cache: OrderedDict[Tuple, MarketDataContract] = OrderedDict()
        self._lock = Lock()
        
        self._hits = 0
        self._misses = 0
        
        logger.info(
            f"DataCache initialized "
            f"(max_size={max_size}, max_age={max_age_seconds}s)"
        )
    
    def put(self, bar: MarketDataContract) -> None:
        """
        Store bar in cache.
        
        Args:
            bar: MarketDataContract
        """
        key = self._make_key(bar.symbol, bar.provider, bar.timestamp)
        
        with self._lock:
            # Add to cache
            self._cache[key] = bar
            self._cache.move_to_end(key)
            
            # LRU eviction if full
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
    
    def get(
        self,
        symbol: str,
        provider: str,
        timestamp: datetime
    ) -> Optional[MarketDataContract]:
        """
        Retrieve bar from cache.
        
        Args:
            symbol: Symbol
            provider: Provider name
            timestamp: Bar timestamp
            
        Returns:
            MarketDataContract or None if not cached/expired
        """
        key = self._make_key(symbol, provider, timestamp)
        
        with self._lock:
            bar = self._cache.get(key)
            
            if bar:
                # Check staleness
                if bar.is_stale(self.max_age_seconds):
                    # Expired - remove
                    del self._cache[key]
                    self._misses += 1
                    return None
                
                # Hit - move to end (LRU)
                self._cache.move_to_end(key)
                self._hits += 1
                return bar
            else:
                self._misses += 1
                return None
    
    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()
        logger.info("DataCache cleared")
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        
        with self._lock:
            size = len(self._cache)
        
        return {
            'size': size,
            'max_size': self.max_size,
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': hit_rate
        }
    
    @staticmethod
    def _make_key(symbol: str, provider: str, timestamp: datetime) -> Tuple:
        """Create cache key."""
        return (symbol.upper(), provider, timestamp.isoformat())
