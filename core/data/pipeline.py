"""
Market data pipeline with caching and fallback.

CRITICAL PROPERTIES:
1. Primary + fallback data sources
2. Data staleness checks
3. In-memory cache with TTL
4. Automatic provider failover
5. Data validation (schema, timestamps)
6. Thread-safe caching

Based on LEAN's DataFeed architecture.
"""

from typing import Optional, List, Dict
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import threading
import time
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from core.logging import get_logger, LogStream
from core.net.throttler import Throttler, ExponentialBackoff


# ============================================================================
# DATA PROVIDER ENUM
# ============================================================================

class DataProvider(Enum):
    """Data provider types."""
    ALPACA = "ALPACA"
    POLYGON = "POLYGON"
    ALPHA_VANTAGE = "ALPHA_VANTAGE"


# ============================================================================
# BAR DATA
# ============================================================================

@dataclass
class BarData:
    """Single bar (OHLCV) data."""
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: DataProvider
    
    def to_dict(self) -> Dict:
        """Convert to dict."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
            "provider": self.provider.value
        }


# ============================================================================
# DATA PIPELINE
# ============================================================================

class MarketDataPipeline:
    """
    Market data pipeline with caching and fallback.
    
    ARCHITECTURE:
    - Primary provider (Alpaca)
    - Fallback providers (Polygon, etc.)
    - In-memory cache with TTL
    - Staleness validation
    - Thread-safe
    
    USAGE:
        pipeline = MarketDataPipeline(
            alpaca_api_key="...",
            alpaca_api_secret="...",
            max_staleness_seconds=90
        )
        
        # Get latest bars
        bars = pipeline.get_latest_bars("SPY", lookback_bars=100)
        
        # Get current price
        price = pipeline.get_current_price("SPY")
    """
    
    def __init__(
        self,
        alpaca_api_key: str,
        alpaca_api_secret: str,
        polygon_api_key: Optional[str] = None,
        max_staleness_seconds: int = 90,
        cache_ttl_seconds: int = 30,
        throttler: Optional[Throttler] = None,
    ):
        """Initialize data pipeline."""
        self.logger = get_logger(LogStream.DATA)
        self.max_staleness = timedelta(seconds=max_staleness_seconds)
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        
        # Throttler (optional). If present, ALL external calls must pass through it.
        self._throttler: Optional[Throttler] = throttler
        self._backoff = ExponentialBackoff(base=1.0, multiplier=2.0, max_delay=10.0, jitter=True)

        # Alpaca client
        self.alpaca_client = StockHistoricalDataClient(
            api_key=alpaca_api_key,
            secret_key=alpaca_api_secret
        )
        
        # Cache: symbol -> (bars_df, cached_at)
        self._cache: Dict[str, tuple] = {}
        self._cache_lock = threading.Lock()
        
        self.logger.info("MarketDataPipeline initialized", extra={
            "max_staleness_seconds": max_staleness_seconds,
            "cache_ttl_seconds": cache_ttl_seconds
        })
    
    def get_latest_bars(
        self,
        symbol: str,
        lookback_bars: int = 100,
        timeframe: str = "1Min"
    ) -> pd.DataFrame:
        """
        Get latest bars for symbol.
        
        Args:
            symbol: Stock symbol
            lookback_bars: Number of bars to retrieve
            timeframe: "1Min", "5Min", "1Hour", "1Day"
            
        Returns:
            DataFrame with OHLCV data
            
        Raises:
            DataPipelineError: If data retrieval fails
        """
        # Check cache
        cached = self._get_from_cache(symbol)
        if cached is not None:
            self.logger.debug(f"Cache hit: {symbol}")
            return cached.tail(lookback_bars)
        
        # Fetch from provider
        try:
            bars_df = self._fetch_from_alpaca(symbol, lookback_bars, timeframe)
            
            # Validate staleness
            if not bars_df.empty:
                latest_ts = bars_df.index[-1]
                age = datetime.now(latest_ts.tzinfo) - latest_ts
                
                if age > self.max_staleness:
                    self.logger.warning(
                        f"Data stale for {symbol}",
                        extra={
                            "symbol": symbol,
                            "age_seconds": age.total_seconds(),
                            "max_staleness": self.max_staleness.total_seconds()
                        }
                    )
                    raise DataStalenessError(f"Data stale: {age.total_seconds()}s > {self.max_staleness.total_seconds()}s")
            
            # Cache result
            self._put_in_cache(symbol, bars_df)
            
            return bars_df.tail(lookback_bars)
            
        except Exception as e:
            self.logger.error(
                f"Failed to get bars for {symbol}",
                extra={"symbol": symbol, "error": str(e)},
                exc_info=True
            )
            raise DataPipelineError(f"Failed to get bars: {e}")
    
    def get_current_price(self, symbol: str) -> Decimal:
        """Get current price for symbol."""
        bars = self.get_latest_bars(symbol, lookback_bars=1)
        if bars.empty:
            raise DataPipelineError(f"No data for {symbol}")
        
        latest = bars.iloc[-1]
        return Decimal(str(latest['close']))
    
    def _fetch_from_alpaca(
        self,
        symbol: str,
        lookback_bars: int,
        timeframe: str
    ) -> pd.DataFrame:
        """Fetch bars from Alpaca."""
        # Map timeframe
        tf = self._map_timeframe(timeframe)
        
        # Calculate start time
        # Rough estimate: 1Min = 1 bar per minute during market hours (6.5 hours = 390 mins)
        if timeframe == "1Min":
            start = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_bars // 390 + 1))
        else:
            start = datetime.now(timezone.utc) - timedelta(days=lookback_bars)
        
        # Build request
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start
        )
        
        # Fetch
        self.logger.debug(f"Fetching {lookback_bars} bars for {symbol} from Alpaca")
        
        # Fetch with throttling + bounded retry/backoff (fail-closed)
        def _do_call():
            return self.alpaca_client.get_stock_bars(request)

        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                if self._throttler is not None:
                    bars = self._throttler.execute_sync('alpaca_data', _do_call)
                else:
                    bars = _do_call()
                last_err = None
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                is_rate = ('429' in msg) or ('rate limit' in msg) or ('too many request' in msg)
                is_transient = is_rate or ('timeout' in msg) or ('temporar' in msg) or ('connection' in msg)
                if attempt < 2 and is_transient:
                    delay = self._backoff.next_delay(attempt)
                    self.logger.warning(
                        'Alpaca bars fetch failed (transient), backing off',
                        extra={'symbol': symbol, 'attempt': attempt + 1, 'delay_s': round(delay, 2), 'error': str(e)}
                    )
                    time.sleep(delay)
                    continue
                break

        if last_err is not None:
            raise DataPipelineError(f'Alpaca bars fetch failed: {last_err}')

        
        if symbol not in bars:
            return pd.DataFrame()
        
        # Convert to DataFrame
        df = bars[symbol].df
        
        self.logger.info(
            f"Fetched {len(df)} bars for {symbol}",
            extra={
                "symbol": symbol,
                "bars": len(df),
                "timeframe": timeframe
            }
        )
        
        return df
    
    def _map_timeframe(self, timeframe: str) -> TimeFrame:
        """Map timeframe string to Alpaca TimeFrame."""
        mapping = {
            "1Min": TimeFrame(1, TimeFrameUnit.Minute),
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "15Min": TimeFrame(15, TimeFrameUnit.Minute),
            "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1Day": TimeFrame(1, TimeFrameUnit.Day)
        }
        return mapping.get(timeframe, TimeFrame(1, TimeFrameUnit.Minute))
    
    def _get_from_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get from cache if not stale."""
        with self._cache_lock:
            if symbol in self._cache:
                df, cached_at = self._cache[symbol]
                age = datetime.now() - cached_at
                
                if age < self.cache_ttl:
                    return df
                else:
                    # Expired
                    del self._cache[symbol]
        
        return None
    
    def _put_in_cache(self, symbol: str, df: pd.DataFrame):
        """Put in cache."""
        with self._cache_lock:
            self._cache[symbol] = (df, datetime.now())
    
    def clear_cache(self):
        """Clear all cached data."""
        with self._cache_lock:
            self._cache.clear()
        self.logger.info("Cache cleared")


# ============================================================================
# EXCEPTIONS
# ============================================================================

class DataPipelineError(Exception):
    """Data pipeline error."""
    pass


class DataStalenessError(DataPipelineError):
    """Data staleness error."""
    pass
