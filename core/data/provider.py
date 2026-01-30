"""
Abstract Market Data Provider Interface.

CRITICAL RULES:
1. All providers MUST return MarketDataContract objects
2. All providers MUST validate data freshness
3. All providers MUST handle rate limits gracefully
4. All providers MUST raise ProviderError on failures
5. NO provider-specific types in return values

This ensures consistent data handling regardless of source.
Based on Hummingbot's connector pattern with enhanced safety.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone
from decimal import Decimal
import logging

from core.data.contract import MarketDataContract

logger = logging.getLogger(__name__)


# ============================================================================
# ABSTRACT PROVIDER INTERFACE
# ============================================================================

class MarketDataProvider(ABC):
    """
    Abstract base for all market data providers.
    
    ALL implementations MUST:
    - Return MarketDataContract objects (not provider-specific types)
    - Validate data freshness before returning
    - Handle rate limits with exponential backoff
    - Raise ProviderError on failures (never return None)
    - Log all API calls for debugging
    
    Example Implementation:
        class AlpacaProvider(MarketDataProvider):
            def get_bars(self, symbol, start, end, timeframe="1Min"):
                raw_bars = self.api.get_bars(...)
                contracts = [self._to_contract(bar) for bar in raw_bars]
                self.validator.validate_bars(contracts)
                return contracts
    """
    
    def __init__(self, name: str, max_staleness_seconds: int = 90):
        """
        Initialize provider.
        
        Args:
            name: Provider name (e.g., "alpaca", "polygon")
            max_staleness_seconds: Maximum acceptable data age
        """
        self.name = name
        self.max_staleness_seconds = max_staleness_seconds
        self._call_count = 0
        self._error_count = 0
        
        logger.info(
            f"Initialized {name} provider (max_staleness={max_staleness_seconds}s)"
        )
    
    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min"
    ) -> List[MarketDataContract]:
        """
        Fetch historical bars.
        
        MUST:
        - Return List[MarketDataContract] sorted by timestamp ascending
        - Validate all bars before returning
        - Check staleness of latest bar
        - Log API call
        
        Args:
            symbol: Ticker symbol (will be normalized to uppercase)
            start: Start datetime (UTC timezone-aware)
            end: End datetime (UTC timezone-aware)
            timeframe: Bar interval ("1Min", "5Min", "15Min", "1Hour", "1Day")
            
        Returns:
            List of MarketDataContract, sorted by timestamp ascending
            
        Raises:
            ProviderError: On API failure, rate limit, invalid data, or staleness
        """
        pass
    
    @abstractmethod
    def get_latest_bar(
        self,
        symbol: str,
        timeframe: str = "1Min"
    ) -> MarketDataContract:
        """
        Fetch most recent bar.
        
        MUST:
        - Return single MarketDataContract
        - Validate bar before returning
        - Check staleness
        - Log API call
        
        Args:
            symbol: Ticker symbol
            timeframe: Bar interval
            
        Returns:
            Most recent MarketDataContract
            
        Raises:
            ProviderError: On API failure, no data, or stale data
        """
        pass
    
    def validate_freshness(
        self,
        bar: MarketDataContract,
        max_age_seconds: Optional[int] = None
    ) -> bool:
        """
        Check if bar is within acceptable staleness threshold.
        
        Args:
            bar: Bar to validate
            max_age_seconds: Override default staleness threshold
            
        Returns:
            True if bar is fresh, False if stale
        """
        threshold = max_age_seconds or self.max_staleness_seconds
        age = bar.age_seconds()
        
        is_fresh = age <= threshold
        
        if not is_fresh:
            logger.warning(
                f"{self.name}: Stale data detected for {bar.symbol} "
                f"(age={age:.1f}s > threshold={threshold}s)"
            )
        
        return is_fresh
    
    def _validate_datetime_params(self, start: datetime, end: datetime) -> None:
        """
        Validate datetime parameters are timezone-aware and in correct order.
        
        Raises:
            ProviderError: If validation fails
        """
        if start.tzinfo is None:
            raise ProviderError(
                f"{self.name}: start datetime must be timezone-aware"
            )
        if end.tzinfo is None:
            raise ProviderError(
                f"{self.name}: end datetime must be timezone-aware"
            )
        if start >= end:
            raise ProviderError(
                f"{self.name}: start ({start}) must be before end ({end})"
            )
    
    def _log_call(self, method: str, symbol: str, **kwargs) -> None:
        """Log API call for debugging."""
        self._call_count += 1
        logger.debug(
            f"{self.name}.{method}(symbol={symbol}, {kwargs}) "
            f"[call #{self._call_count}]"
        )
    
    def _log_error(self, method: str, error: Exception) -> None:
        """Log API error."""
        self._error_count += 1
        logger.error(
            f"{self.name}.{method} failed: {error} "
            f"[error #{self._error_count}]",
            exc_info=True
        )
    
    def get_stats(self) -> dict:
        """
        Get provider statistics.
        
        Returns:
            Dict with call_count, error_count, error_rate
        """
        error_rate = (
            self._error_count / self._call_count
            if self._call_count > 0
            else 0.0
        )
        
        return {
            'name': self.name,
            'call_count': self._call_count,
            'error_count': self._error_count,
            'error_rate': error_rate
        }


# ============================================================================
# EXCEPTIONS
# ============================================================================

class ProviderError(Exception):
    """
    Raised on data provider failures.
    
    Covers:
    - API errors
    - Rate limits
    - Invalid data
    - Stale data
    - Network failures
    """
    pass
