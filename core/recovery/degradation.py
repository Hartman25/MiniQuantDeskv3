"""
Graceful degradation system for data providers.

ARCHITECTURE:
- Multiple data provider fallback chains
- Automatic failover on provider errors
- Provider health tracking
- Smart retry logic with backoff
- Stale data detection

PROVIDERS (Priority Order):
1. Primary: Alpaca (real-time)
2. Fallback 1: Polygon (near real-time)
3. Fallback 2: Finnhub (delayed)
4. Last resort: Cached data (with staleness warning)

SAFETY:
- Never fail completely (always return data)
- Mark data staleness clearly
- Alert on provider failures
- Automatic recovery when provider comes back

Based on LEAN's data redundancy and Freqtrade's exchange fallback.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Callable, Any
from enum import Enum
from collections import deque

from core.logging import get_logger, LogStream


# ============================================================================
# PROVIDER STATUS
# ============================================================================

class ProviderStatus(Enum):
    """Data provider status."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"  # Working but slow
    FAILED = "FAILED"


@dataclass
class ProviderHealth:
    """Health status of a data provider."""
    name: str
    status: ProviderStatus
    last_success: Optional[datetime]
    last_failure: Optional[datetime]
    consecutive_failures: int
    success_rate: float  # Last 100 requests
    avg_latency_ms: float
    
    def is_healthy(self) -> bool:
        """Check if provider is healthy."""
        return self.status == ProviderStatus.HEALTHY
    
    def is_usable(self) -> bool:
        """Check if provider can be used (not failed)."""
        return self.status in [ProviderStatus.HEALTHY, ProviderStatus.DEGRADED]


@dataclass
class QuoteData:
    """Quote data with metadata."""
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime
    provider: str
    is_stale: bool = False
    latency_ms: Optional[float] = None
    
    def age_seconds(self) -> float:
        """Calculate data age in seconds."""
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()


# ============================================================================
# RESILIENT DATA PROVIDER
# ============================================================================

class ResilientDataProvider:
    """
    Data provider with automatic fallback and recovery.
    
    RESPONSIBILITIES:
    - Try providers in priority order
    - Track provider health
    - Automatic failover on errors
    - Smart retry with backoff
    - Stale data detection
    
    FALLBACK CHAIN:
    1. Primary provider (Alpaca)
    2. Secondary provider (Polygon)
    3. Tertiary provider (Finnhub)
    4. Cached data (stale)
    
    USAGE:
        provider = ResilientDataProvider(
            primary=alpaca_provider,
            fallbacks=[polygon_provider, finnhub_provider]
        )
        
        # Get quote with automatic fallback
        quote = provider.get_quote("SPY")
        
        if quote.is_stale:
            logger.warning("Using stale data")
    """
    
    def __init__(
        self,
        primary_provider,
        fallback_providers: Optional[List] = None,
        staleness_threshold_seconds: int = 60,
        cache_ttl_seconds: int = 300,
        max_retries: int = 2
    ):
        """
        Initialize resilient provider.
        
        Args:
            primary_provider: Primary data provider
            fallback_providers: List of fallback providers (in order)
            staleness_threshold_seconds: Data older than this is stale
            cache_ttl_seconds: Cache TTL for last-resort data
            max_retries: Max retries per provider
        """
        self.primary = primary_provider
        self.fallbacks = fallback_providers or []
        self.staleness_threshold = timedelta(seconds=staleness_threshold_seconds)
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.max_retries = max_retries
        
        self.logger = get_logger(LogStream.DATA)
        
        # All providers (primary + fallbacks)
        self.all_providers = [self.primary] + self.fallbacks
        
        # Provider health tracking
        self.health: Dict[str, ProviderHealth] = {}
        for provider in self.all_providers:
            provider_name = getattr(provider, 'name', provider.__class__.__name__)
            self.health[provider_name] = ProviderHealth(
                name=provider_name,
                status=ProviderStatus.HEALTHY,
                last_success=None,
                last_failure=None,
                consecutive_failures=0,
                success_rate=1.0,
                avg_latency_ms=0.0
            )
        
        # Request history for success rate calculation
        self._request_history: Dict[str, deque] = {
            name: deque(maxlen=100) for name in self.health.keys()
        }
        
        # Data cache (last-resort)
        self._cache: Dict[str, QuoteData] = {}
        
        self.logger.info("ResilientDataProvider initialized", extra={
            "primary": self.primary.__class__.__name__,
            "fallbacks": [p.__class__.__name__ for p in self.fallbacks],
            "staleness_threshold_sec": staleness_threshold_seconds
        })
    
    # ========================================================================
    # DATA RETRIEVAL WITH FALLBACK
    # ========================================================================
    
    def get_quote(self, symbol: str) -> Optional[QuoteData]:
        """
        Get quote with automatic fallback.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            QuoteData (possibly stale) or None
        """
        # Try each provider in order
        for provider in self.all_providers:
            provider_name = getattr(provider, 'name', provider.__class__.__name__)
            
            # Skip failed providers
            if not self.health[provider_name].is_usable():
                continue
            
            # Try to get quote
            quote = self._try_provider(provider, symbol)
            
            if quote:
                # Update cache
                self._cache[symbol] = quote
                return quote
        
        # All providers failed - try cache
        cached = self._get_cached_quote(symbol)
        if cached:
            self.logger.warning(
                f"All providers failed, using cached data: {symbol}",
                extra={
                    "symbol": symbol,
                    "cache_age_seconds": cached.age_seconds()
                }
            )
            return cached
        
        # No data available
        self.logger.error(f"No data available for {symbol}")
        return None
    
    def _try_provider(
        self,
        provider,
        symbol: str
    ) -> Optional[QuoteData]:
        """Try to get quote from a specific provider."""
        provider_name = getattr(provider, 'name', provider.__class__.__name__)
        
        for attempt in range(self.max_retries):
            try:
                start_time = datetime.now(timezone.utc)
                
                # Get quote from provider
                quote_data = provider.get_quote(symbol)
                
                if quote_data:
                    # Calculate latency
                    latency = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                    
                    # Create QuoteData
                    quote = QuoteData(
                        symbol=symbol,
                        bid=Decimal(str(quote_data.get('bid', 0))),
                        ask=Decimal(str(quote_data.get('ask', 0))),
                        last=Decimal(str(quote_data.get('last', 0))),
                        timestamp=quote_data.get('timestamp', datetime.now(timezone.utc)),
                        provider=provider_name,
                        latency_ms=latency
                    )
                    
                    # Check staleness
                    if quote.age_seconds() > self.staleness_threshold.total_seconds():
                        quote.is_stale = True
                        self.logger.warning(
                            f"Stale data from {provider_name}",
                            extra={
                                "symbol": symbol,
                                "age_seconds": quote.age_seconds()
                            }
                        )
                    
                    # Record success
                    self._record_success(provider_name, latency)
                    
                    return quote
                    
            except Exception as e:
                self.logger.warning(
                    f"Provider {provider_name} failed (attempt {attempt+1}/{self.max_retries})",
                    extra={
                        "provider": provider_name,
                        "symbol": symbol,
                        "error": str(e)
                    }
                )
                
                # Record failure
                self._record_failure(provider_name)
                
                # Don't retry if provider is now marked as failed
                if not self.health[provider_name].is_usable():
                    break
        
        return None
    
    def _get_cached_quote(self, symbol: str) -> Optional[QuoteData]:
        """Get quote from cache (last resort)."""
        cached = self._cache.get(symbol)
        
        if not cached:
            return None
        
        # Check cache TTL
        if cached.age_seconds() > self.cache_ttl.total_seconds():
            return None  # Too stale
        
        # Mark as stale
        cached.is_stale = True
        return cached
    
    # ========================================================================
    # HEALTH TRACKING
    # ========================================================================
    
    def _record_success(self, provider_name: str, latency_ms: float):
        """Record successful request."""
        health = self.health[provider_name]
        
        # Update timestamps
        health.last_success = datetime.now(timezone.utc)
        health.consecutive_failures = 0
        
        # Update status
        if health.status == ProviderStatus.FAILED:
            health.status = ProviderStatus.HEALTHY
            self.logger.info(f"Provider {provider_name} recovered")
        
        # Record in history
        self._request_history[provider_name].append(True)
        
        # Update success rate
        history = self._request_history[provider_name]
        health.success_rate = sum(history) / len(history) if history else 1.0
        
        # Update latency (exponential moving average)
        alpha = 0.3
        health.avg_latency_ms = (alpha * latency_ms) + ((1 - alpha) * health.avg_latency_ms)
    
    def _record_failure(self, provider_name: str):
        """Record failed request."""
        health = self.health[provider_name]
        
        # Update timestamps
        health.last_failure = datetime.now(timezone.utc)
        health.consecutive_failures += 1
        
        # Record in history
        self._request_history[provider_name].append(False)
        
        # Update success rate
        history = self._request_history[provider_name]
        health.success_rate = sum(history) / len(history) if history else 0.0
        
        # Update status based on consecutive failures
        if health.consecutive_failures >= 3:
            health.status = ProviderStatus.FAILED
            self.logger.error(f"Provider {provider_name} marked as FAILED")
        elif health.consecutive_failures >= 2:
            health.status = ProviderStatus.DEGRADED
            self.logger.warning(f"Provider {provider_name} marked as DEGRADED")
    
    def get_provider_health(self, provider_name: str) -> Optional[ProviderHealth]:
        """Get health status of a provider."""
        return self.health.get(provider_name)
    
    def get_all_health(self) -> Dict[str, ProviderHealth]:
        """Get health status of all providers."""
        return self.health.copy()
    
    def get_healthy_providers(self) -> List[str]:
        """Get list of healthy provider names."""
        return [
            name for name, health in self.health.items()
            if health.is_healthy()
        ]
    
    # ========================================================================
    # MANUAL CONTROL
    # ========================================================================
    
    def reset_provider(self, provider_name: str):
        """Manually reset a provider's health status."""
        if provider_name in self.health:
            health = self.health[provider_name]
            health.status = ProviderStatus.HEALTHY
            health.consecutive_failures = 0
            self.logger.info(f"Reset provider: {provider_name}")
