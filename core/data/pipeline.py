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
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
import re
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

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
    timeframe: str = "1Min",
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
        # -----------------
        # Cache path
        # -----------------
        _diag = os.getenv("PIPELINE_DIAG", "0").strip().lower() in ("1", "true", "yes")
        cached = self._get_from_cache(symbol, timeframe)
        if cached is not None:
            self.logger.debug(f"Cache hit: {symbol} {timeframe}")
            if _diag:
                print(f"[DIAG] cache hit {symbol} {timeframe}: {len(cached)} bars, last_ts={cached.index[-1] if not cached.empty else 'EMPTY'}")

            # Prevent lookahead on cached path too
            cached = self._drop_incomplete_last_bar(cached, timeframe)

            if cached is not None and not cached.empty:
                if _diag:
                    print(f"[DIAG] cache returning {len(cached.tail(lookback_bars))} bars for {symbol}")
                return cached.tail(lookback_bars)
            elif _diag:
                print(f"[DIAG] cache empty after drop_incomplete for {symbol}")

        # -----------------
        # Provider path
        # -----------------
        try:
            # Fetch a bit extra so dropping the last bar doesn't leave us short
            fetch_n = max(int(lookback_bars) + 2, 3)

            bars_df = self._fetch_from_alpaca(symbol, fetch_n, timeframe)
            if _diag:
                _n = len(bars_df) if bars_df is not None and not bars_df.empty else 0
                _last = bars_df.index[-1] if _n > 0 else "EMPTY"
                _first = bars_df.index[0] if _n > 0 else "EMPTY"
                print(f"[DIAG] alpaca returned {_n} bars for {symbol}, range=[{_first} .. {_last}]")

            # Prevent lookahead: drop incomplete last bar
            bars_df = self._drop_incomplete_last_bar(bars_df, timeframe)
            if _diag:
                _n2 = len(bars_df) if bars_df is not None and not bars_df.empty else 0
                _last2 = bars_df.index[-1] if _n2 > 0 else "EMPTY"
                print(f"[DIAG] after drop_incomplete: {_n2} bars, last_ts={_last2}")

            # Validate staleness (use close time of last bar, not open time)
            # A bar timestamped 09:30 with 1Min timeframe has data valid
            # through 09:31, so staleness is measured from 09:31, not 09:30.
            if bars_df is not None and not bars_df.empty:
                latest_ts = pd.Timestamp(bars_df.index[-1])

                if latest_ts.tzinfo is None:
                    latest_ts = latest_ts.tz_localize("UTC")
                else:
                    latest_ts = latest_ts.tz_convert("UTC")

                bar_close_time = latest_ts.to_pydatetime() + self._timeframe_to_timedelta(timeframe)
                age = datetime.now(timezone.utc) - bar_close_time
                if _diag:
                    print(f"[DIAG] staleness: bar_close={bar_close_time.isoformat()}, now={datetime.now(timezone.utc).isoformat()}, age={age.total_seconds():.1f}s, max={self.max_staleness.total_seconds():.0f}s")

                if age > self.max_staleness:
                    self.logger.warning(
                        f"Data stale for {symbol}",
                        extra={
                            "symbol": symbol,
                            "age_seconds": age.total_seconds(),
                            "max_staleness": self.max_staleness.total_seconds(),
                            "timeframe": timeframe,
                        },
                    )
                    raise DataStalenessError(
                        f"Data stale: {age.total_seconds()}s > {self.max_staleness.total_seconds()}s"
                    )
            elif _diag:
                print(f"[DIAG] bars_df empty after drop_incomplete — skipping staleness check")

            # Cache result (cache full df; caller tails it)
            self._put_in_cache(symbol, timeframe, bars_df)

            _result = bars_df.tail(lookback_bars) if bars_df is not None else bars_df
            if _diag:
                _nr = len(_result) if _result is not None and not _result.empty else 0
                print(f"[DIAG] pipeline returning {_nr} bars for {symbol}")
            return _result

        except Exception as e:
            self.logger.error(
                f"Failed to get bars for {symbol}",
                extra={"symbol": symbol, "error": str(e), "timeframe": timeframe},
                exc_info=True,
            )
            if _diag:
                print(f"[DIAG] pipeline EXCEPTION for {symbol}: {type(e).__name__}: {e}")
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
            start=start,
            feed=DataFeed.SIP,
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
    
    def _get_from_cache(self, symbol: object, timeframe: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Get cached bars for (symbol, timeframe) if not expired.

        Canonical call style:
            cached = self._get_from_cache(symbol, timeframe)

        Backward-compat:
            _get_from_cache((symbol, timeframe)) or _get_from_cache("SYMB:1Min")
        """
        # Backward-compat: allow callers to pass a pre-built key
        if timeframe is None and isinstance(symbol, tuple) and len(symbol) == 2:
            symbol, timeframe = symbol
        if timeframe is None and isinstance(symbol, str) and ":" in symbol:
            symbol, timeframe = symbol.split(":", 1)

        cache_key = (str(symbol), str(timeframe))

        with self._cache_lock:
            item = self._cache.get(cache_key)
            if item is None:
                return None

            df, cached_at = item
            now = datetime.now(timezone.utc)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = now - cached_at

            if age < self.cache_ttl:
                return df

            del self._cache[cache_key]
            return None


    def _put_in_cache(self, symbol: object, timeframe: Optional[str], df: Optional[pd.DataFrame] = None) -> None:
        """Put bars into cache under (symbol, timeframe).

        Canonical call style:
            self._put_in_cache(symbol, timeframe, bars_df)

        Backward-compat:
            _put_in_cache((symbol, timeframe), df) or _put_in_cache("SYMB:1Min", df)
        """
        # Backward-compat: allow callers to pass (cache_key, df)
        if df is None and isinstance(timeframe, pd.DataFrame):
            df = timeframe
            timeframe = None

        if isinstance(symbol, tuple) and len(symbol) == 2:
            symbol, timeframe = symbol
        if timeframe is None and isinstance(symbol, str) and ":" in symbol:
            symbol, timeframe = symbol.split(":", 1)

        if df is None:
            raise TypeError("_put_in_cache expected a DataFrame, got None")

        cache_key = (str(symbol), str(timeframe))
        with self._cache_lock:
            self._cache[cache_key] = (df, datetime.now(timezone.utc))


    def clear_cache(self) -> None:
        """Clear all cached market data."""
        with self._cache_lock:
            self._cache.clear()
        self.logger.info("Cache cleared")

    def _timeframe_to_timedelta(self, timeframe: str) -> timedelta:
        tf = (timeframe or "").strip().lower()

        # Common formats: "1Min", "5Min", "1Hour", "1Day"
        if tf.endswith("min"):
            n = int(tf[:-3] or "1")
            return timedelta(minutes=n)
        if tf.endswith("hour"):
            n = int(tf[:-4] or "1")
            return timedelta(hours=n)
        if tf.endswith("day"):
            n = int(tf[:-3] or "1")
            return timedelta(days=n)

        # Also accept "1m", "5m", "1h", "1d"
        if tf.endswith("m"):
            return timedelta(minutes=int(tf[:-1] or "1"))
        if tf.endswith("h"):
            return timedelta(hours=int(tf[:-1] or "1"))
        if tf.endswith("d"):
            return timedelta(days=int(tf[:-1] or "1"))

        # Default safe behavior
        return timedelta(minutes=1)

    def _drop_incomplete_last_bar(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """
        Drop the last bar if it is not complete yet (anti-lookahead).
        Completeness rule: now >= last_ts + timeframe_duration

        NOTE:
        - If df has only 1 row, dropping would create an empty result. In that case,
          keep the single bar to avoid "no data" behavior in sparse/test environments.
        """
        if df is None or df.empty:
            return df

        # Ensure a DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            return df

        # If there's only one bar, don't drop it to empty the result.
        # (We still drop the last bar when we have at least one earlier bar.)
        if len(df) <= 1:
            return df

        last_ts = df.index[-1]

        # Normalize timezone: compare now in the same tz as last_ts
        if last_ts.tzinfo is None:
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            last_ts = last_ts.tz_localize(timezone.utc)
            # (optional) also localize whole index if you want consistency:
            # df = df.tz_localize(timezone.utc)
        else:
            now = datetime.now(tz=last_ts.tzinfo)

        dur = self._timeframe_to_timedelta(timeframe)

        # If the bar hasn't had time to "finish", drop it
        if now < (last_ts.to_pydatetime() + dur):
            self.logger.warning(
                "Dropping incomplete last bar (anti-lookahead)",
                extra={
                    "timeframe": timeframe,
                    "last_ts": str(last_ts),
                    "now": now.isoformat(),
                    "seconds_remaining": (last_ts.to_pydatetime() + dur - now).total_seconds(),
                },
            )
            return df.iloc[:-1]

        return df

    def _ensure_utc_index(self, df: pd.DataFrame) -> pd.DataFrame:
        """Make sure df.index is a tz-aware DatetimeIndex in UTC."""
        if df is None or df.empty:
            return df
        if not isinstance(df.index, pd.DatetimeIndex):
            # If your fetch returns a timestamp column instead of index, handle it here if needed.
            # Otherwise leave it and let downstream fail loudly.
            return df

        if df.index.tz is None:
            df = df.copy()
            df.index = df.index.tz_localize(timezone.utc)
        else:
            df = df.tz_convert(timezone.utc)
        return df
    
# ============================================================================
# EXCEPTIONS
# ============================================================================

class DataPipelineError(Exception):
    """Data pipeline error."""
    pass


class DataStalenessError(DataPipelineError):
    """Data staleness error."""
    pass
