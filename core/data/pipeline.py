# ============================================
# MiniQuantDesk :: Market Data Pipeline
# ============================================
"""
Market data pipeline with caching + validation helpers.

Phase-1 goal:
- Always return CLOSED bars (anti-lookahead).
- Fail closed on malformed data.
- Allow provider failover so you can run paper validation without paying SIP.

Providers:
- Alpaca (existing)
- TwelveData (new fallback/primary if TWELVEDATA_API_KEY is set)

Notes:
- Alpaca IEX can be delayed / incomplete depending on plan/symbol/feed.
- TwelveData is used to obtain 1-min bars cheaply/quickly without rewiring later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

from decimal import Decimal
from datetime import datetime, timedelta, timezone

import os
import re
import threading
import time

import pandas as pd
import requests

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

try:
    # alpaca-py
    from alpaca.data.enums import DataFeed
except Exception:  # pragma: no cover
    DataFeed = None

from core.logging import get_logger, LogStream
from core.net.throttler import Throttler, ExponentialBackoff


# ============================================================================
# PROVIDERS
# ============================================================================

class DataProvider(Enum):
    ALPACA = "alpaca"
    POLYGON = "polygon"
    ALPHA_VANTAGE = "alpha_vantage"
    TWELVEDATA = "twelvedata"


# ============================================================================
# EXCEPTIONS
# ============================================================================

class DataPipelineError(Exception):
    pass


class DataStalenessError(DataPipelineError):
    pass


# ============================================================================
# DATA OBJECTS
# ============================================================================

@dataclass
class CacheEntry:
    data: pd.DataFrame
    timestamp: datetime
    provider: DataProvider


# ============================================================================
# MARKET DATA PIPELINE
# ============================================================================

class MarketDataPipeline:
    """
    Fetches OHLCV bar data for symbols with:
    - In-memory caching
    - Provider failover
    - Staleness checks (policy-controlled)
    - Closed-bar enforcement (anti-lookahead)

    IMPORTANT:
    - This pipeline returns *bars* only.
    - Phase-1 "valid to trade?" decisions happen in DataValidator.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        alpaca_api_key: Optional[str] = None,
        alpaca_api_secret: Optional[str] = None,
        twelvedata_api_key: Optional[str] = None,
        throttler: Optional[Throttler] = None,
        max_staleness: timedelta = timedelta(seconds=65),
        cache_ttl: timedelta = timedelta(seconds=30),
        # legacy config keys (container may still call these)
        max_staleness_seconds: Optional[int] = None,
        cache_ttl_seconds: Optional[int] = None,
        allow_gaps: bool = True,
        gap_tolerance_pct: float = 5.0,
        require_complete: bool = True,
        feed: Optional[str] = None,
        # NEW: provider routing
        primary_provider: Optional[object] = None,
        fallback_providers: Optional[List[object]] = None,
        # NEW: stale policy (container passes this)
        allow_stale_in_paper: bool = True,
        # NEW: throttler limit ids
        alpaca_limit_id: str = "alpaca_data",
        twelvedata_limit_id: str = "twelvedata_data",
    ) -> None:
        self.logger = get_logger(LogStream.DATA)

        # Back-compat: allow container/config to pass seconds
        if max_staleness_seconds is not None:
            max_staleness = timedelta(seconds=int(max_staleness_seconds))
        if cache_ttl_seconds is not None:
            cache_ttl = timedelta(seconds=int(cache_ttl_seconds))

        # Resolve Alpaca credentials (support both api_key/api_secret and alpaca_* legacy)
        api_key = api_key or alpaca_api_key or os.getenv("ALPACA_API_KEY") or os.getenv("BROKER_API_KEY")
        api_secret = api_secret or alpaca_api_secret or os.getenv("ALPACA_API_SECRET") or os.getenv("BROKER_API_SECRET")
        if not api_key or not api_secret:
            raise DataPipelineError("Missing Alpaca credentials (api_key/api_secret). Check .env.local.")

        # IMPORTANT: do NOT implicitly read env vars here.
        # The Container/config layer should pass twelvedata_api_key explicitly.
        self.twelvedata_api_key = (twelvedata_api_key or "").strip() or None

        # Alpaca client (primary)
        self.alpaca_client = StockHistoricalDataClient(api_key, api_secret)

        # Behavior configuration
        self.max_staleness = max_staleness
        self.cache_ttl = cache_ttl

        # IMPORTANT:
        # cache_ttl_seconds <= 0 means cache disabled.
        # Tests rely on this to force throttler execution every call.
        self._cache_enabled = float(self.cache_ttl.total_seconds()) > 0.0

        self.allow_gaps = bool(allow_gaps)
        self.gap_tolerance_pct = float(gap_tolerance_pct)
        self.require_complete = bool(require_complete)

        # Stale policy
        self.allow_stale_in_paper = bool(allow_stale_in_paper)

        # Throttling / backoff
        self.throttler = throttler
        self.backoff = ExponentialBackoff(base=0.5, multiplier=2.0, max_delay=15.0, jitter=True)

        # Throttler bucket ids (TEST CONTRACT: alpaca must default to "alpaca_data")
        self._alpaca_limit_id = str(alpaca_limit_id or "alpaca_data")
        self._twelvedata_limit_id = str(twelvedata_limit_id or "twelvedata_data")

        # Alpaca feed selection (IEX vs SIP)
        self._alpaca_feed = self._select_alpaca_feed(feed)

        # ----------------------------
        # Provider routing (DEFAULTS MATTER)
        # Default primary MUST be ALPACA unless explicitly configured otherwise.
        # ----------------------------
        def _coerce_provider(p: Optional[object]) -> Optional[DataProvider]:
            if p is None:
                return None
            if isinstance(p, DataProvider):
                return p
            s = str(p).strip().lower()
            if s in ("alpaca", "dataprovider.alpaca"):
                return DataProvider.ALPACA
            if s in ("twelvedata", "twelve_data", "twelve-data", "dataprovider.twelvedata"):
                return DataProvider.TWELVEDATA
            if s in ("polygon", "dataprovider.polygon"):
                return DataProvider.POLYGON
            if s in ("yfinance", "dataprovider.yfinance"):
                return DataProvider.YFINANCE
            return None

        primary = _coerce_provider(primary_provider) or DataProvider.ALPACA

        fallbacks: List[DataProvider] = []
        if fallback_providers:
            for fp in fallback_providers:
                c = _coerce_provider(fp)
                if c and c not in fallbacks:
                    fallbacks.append(c)

        # If primary is TWELVEDATA but no key was passed, fail over to ALPACA as primary.
        if primary == DataProvider.TWELVEDATA and not self.twelvedata_api_key:
            primary = DataProvider.ALPACA

        # Build provider sequence: primary first, then fallbacks, then ensure Alpaca last-resort fallback.
        seq: List[DataProvider] = [primary]
        for fp in fallbacks:
            if fp == primary:
                continue
            if fp == DataProvider.TWELVEDATA and not self.twelvedata_api_key:
                continue
            seq.append(fp)
        if DataProvider.ALPACA not in seq:
            seq.append(DataProvider.ALPACA)

        self.provider_sequence = seq

        # Cache: key should include symbol+timeframe+lookback so tests don't get accidental hits
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_lock = threading.Lock()

        self.logger.info(
            "MarketDataPipeline initialized",
            extra={
                "max_staleness_seconds": float(self.max_staleness.total_seconds()),
                "cache_ttl_seconds": float(self.cache_ttl.total_seconds()),
                "allow_gaps": self.allow_gaps,
                "gap_tolerance_pct": self.gap_tolerance_pct,
                "require_complete": self.require_complete,
                "allow_stale_in_paper": self.allow_stale_in_paper,
                "twelvedata_enabled": bool(self.twelvedata_api_key),
                "alpaca_feed": getattr(self._alpaca_feed, "value", str(self._alpaca_feed)),
                "providers": [getattr(p, "value", str(p)) for p in self.provider_sequence],
                "alpaca_limit_id": self._alpaca_limit_id,
                "twelvedata_limit_id": self._twelvedata_limit_id,
            },
        )

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    def get_latest_bars(
        self,
        symbol: str,
        lookback_bars: int = 2,
        timeframe: str = "1Min",
        force_refresh: bool = False,
    ) -> Optional[pd.DataFrame]:
        """
        Get latest OHLCV bars for a symbol.

        Returns DataFrame indexed by timestamp with columns:
        open, high, low, close, volume
        """
        cache_key = f"{symbol}_{timeframe}"

        # 1) cache
        if self._cache_enabled and not force_refresh:
            cached = self._get_cached(cache_key)
            if cached is not None and not cached.empty:
                # Always respect requested lookback even if cache stores more.
                return cached.tail(int(lookback_bars)).copy()

        tf_obj, tf_label = self._normalize_timeframe(timeframe)

        # 2) providers
        last_error: Optional[Exception] = None

        # Fetch a little extra so we can drop last incomplete bar and still have enough
        fetch_n = max(int(lookback_bars) + 2, 3)

        for provider in self.provider_sequence:
            try:
                bars_df = self._fetch_bars(symbol, fetch_n, tf_obj, tf_label, provider)

                if bars_df is None or bars_df.empty:
                    self.logger.info("[data] %s: no_closed_bars_available", symbol, extra={"symbol": symbol})
                    continue

                # Normalize & enforce closed bars
                bars_df = self._drop_incomplete_last_bar(bars_df, tf_label)
                if bars_df is None or bars_df.empty:
                    self.logger.info("[data] %s: only_incomplete_bar_available", symbol, extra={"symbol": symbol})
                    continue

                # Validate
                if not self._validate_bars(bars_df, symbol, tf_label):
                    self.logger.warning("[data] %s: validation_failed", symbol, extra={"symbol": symbol})
                    continue

                # Staleness check (policy-controlled)
                latest_ts = self._safe_last_timestamp(bars_df)
                now_utc = datetime.now(timezone.utc)
                age = now_utc - latest_ts
                if age > self.max_staleness:
                    if self._stale_allowed():
                        self.logger.warning(
                            "Data stale for %s (allowed by policy); continuing with delayed bars",
                            symbol,
                            extra={
                                "symbol": symbol,
                                "age_seconds": age.total_seconds(),
                                "max_staleness_seconds": self.max_staleness.total_seconds(),
                                "timeframe": tf_label,
                                "provider": provider.value,
                            },
                        )
                    else:
                        raise DataStalenessError(
                            f"Data stale: {age.total_seconds()}s > {self.max_staleness.total_seconds()}s"
                        )

                # Cache & return
                self._set_cached(cache_key, bars_df, provider)
                return bars_df.tail(int(lookback_bars))

            except Exception as e:
                last_error = e
                self.logger.warning(
                    "Failed to get bars from %s for %s: %s",
                    provider.value,
                    symbol,
                    e,
                    extra={"symbol": symbol, "provider": provider.value},
                    exc_info=True,
                )
                continue

        self.logger.error("Failed to get bars for %s", symbol, extra={"symbol": symbol, "error": str(last_error)}, exc_info=True)
        return None

    def get_current_price(self, symbol: str) -> Decimal:
        bars = self.get_latest_bars(symbol, lookback_bars=1)
        if bars is None or bars.empty:
            raise DataPipelineError(f"No data for {symbol}")
        return Decimal(str(bars.iloc[-1]["close"]))

    # ============================================================================
    # PROVIDER DISPATCH
    # ============================================================================

    def _fetch_bars(
        self,
        symbol: str,
        lookback_bars: int,
        tf_obj: Any,
        tf_label: str,
        provider: DataProvider,
    ) -> pd.DataFrame:
        if provider == DataProvider.ALPACA:
            return self._fetch_from_alpaca(symbol, lookback_bars, tf_obj, tf_label)
        elif provider == DataProvider.TWELVEDATA:
            return self._fetch_from_twelvedata(symbol, lookback_bars, tf_label)
        elif provider == DataProvider.POLYGON:
            raise NotImplementedError("Polygon provider not implemented")
        elif provider == DataProvider.ALPHA_VANTAGE:
            raise NotImplementedError("Alpha Vantage provider not implemented")
        else:
            raise DataPipelineError(f"Unknown provider: {provider}")

    # ============================================================================
    # TWELVEDATA
    # ============================================================================

    def _fetch_from_twelvedata(self, symbol: str, lookback_bars: int, tf_label: str) -> pd.DataFrame:
        """
        Fetch bars from Twelve Data (https://twelvedata.com) time_series endpoint.

        Notes:
        - Twelve Data returns JSON with 'values' as strings.
        - We request timezone=UTC to standardize timestamps.
        - Returned DataFrame index is UTC tz-aware DatetimeIndex.
        """
        if not self.twelvedata_api_key:
            raise DataPipelineError("TWELVEDATA provider selected but TWELVEDATA_API_KEY is not set")

        try:
            lookback_bars_int = int(lookback_bars)
        except Exception as e:
            raise DataPipelineError(f"lookback_bars must be int-like, got {lookback_bars!r}: {e}") from e
        if lookback_bars_int <= 0:
            raise DataPipelineError(f"lookback_bars must be > 0, got {lookback_bars_int}")

        interval = self._twelvedata_interval(tf_label)

        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": str(min(max(lookback_bars_int, 2), 5000)),
            "timezone": "UTC",
            "format": "JSON",
            "apikey": self.twelvedata_api_key,
        }

        url = "https://api.twelvedata.com/time_series"

        def _call():
            return requests.get(url, params=params, timeout=15)

        last_err: Optional[Exception] = None
        resp = None
        for attempt in range(3):
            try:
                # If a throttler exists, try to route via an existing low-frequency bucket (fmp),
                # otherwise call directly. This avoids requiring a new throttler key.
                if self.throttler is not None and hasattr(self.throttler, "execute_sync"):
                    resp = self.throttler.execute_sync(self._twelvedata_limit_id, _call)
                else:
                    resp = _call()

                if resp.status_code >= 400:
                    raise DataPipelineError(f"TwelveData HTTP {resp.status_code}: {resp.text[:200]}")
                last_err = None
                break
            except Exception as e:
                last_err = e
                delay = self.backoff.next_delay(attempt)
                msg = str(e).lower()
                transient = any(k in msg for k in ("429", "rate", "timeout", "temporar", "connection", "reset", "502", "503", "504"))
                if attempt < 2 and transient:
                    self.logger.warning(
                        "TwelveData bars fetch failed (transient), backing off",
                        extra={"symbol": symbol, "attempt": attempt + 1, "delay_s": round(delay, 2), "error": str(e)},
                    )
                    time.sleep(delay)
                    continue
                break

        if last_err is not None:
            raise DataPipelineError(f"TwelveData bars fetch failed: {last_err}")

        try:
            payload = resp.json()
        except Exception as e:
            raise DataPipelineError(f"TwelveData JSON decode failed: {e}") from e

        if isinstance(payload, dict) and payload.get("status") == "error":
            raise DataPipelineError(f"TwelveData error: {payload.get('message') or payload}")

        values = payload.get("values") if isinstance(payload, dict) else None
        if not values:
            return pd.DataFrame()

        rows: List[Dict[str, Any]] = []
        for v in reversed(values):  # newest-first -> oldest-first
            try:
                ts = pd.to_datetime(v.get("datetime"), utc=True)
                rows.append(
                    {
                        "timestamp": ts,
                        "open": float(v.get("open")),
                        "high": float(v.get("high")),
                        "low": float(v.get("low")),
                        "close": float(v.get("close")),
                        "volume": float(v.get("volume") or 0.0),
                    }
                )
            except Exception:
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("timestamp")
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.sort_index()

        self.logger.info(
            "Fetched %s bars for %s (TwelveData)",
            len(df),
            symbol,
            extra={"symbol": symbol, "bars": int(len(df)), "timeframe": tf_label, "provider": "twelvedata"},
        )
        return df

    def _twelvedata_interval(self, tf_label: str) -> str:
        """Convert internal timeframe label (e.g. '1Min') to TwelveData interval string."""
        tf = str(tf_label or "").strip().lower()
        if tf in ("1min", "1m", "minute", "1 minute"):
            return "1min"
        if tf in ("5min", "5m", "5 minutes"):
            return "5min"
        if tf in ("15min", "15m", "15 minutes"):
            return "15min"
        if tf in ("1hour", "1h", "60min", "60m"):
            return "1h"
        if tf in ("1day", "day", "1d"):
            return "1day"

        m = re.match(r"^(\d+)\s*(min|m|h|hr|hour|d|day)s?$", tf)
        if not m:
            return "1min"
        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("min", "m"):
            return f"{n}min"
        if unit in ("h", "hr", "hour"):
            return f"{n}h"
        if unit in ("d", "day"):
            return f"{n}day"
        return "1min"

    # ============================================================================
    # ALPACA
    # ============================================================================

    def _fetch_from_alpaca(self, symbol: str, lookback_bars: int, tf_obj: Any, tf_label: str) -> pd.DataFrame:
        """
        Fetch bars from Alpaca using alpaca-py.

        Normalizes DataFrame index to a simple DatetimeIndex (UTC) even if alpaca returns MultiIndex.
        """
        # Harden: defensive coercion
        try:
            lookback_bars_int = int(lookback_bars)
        except Exception as e:
            raise DataPipelineError(f"lookback_bars must be int-like, got {lookback_bars!r}: {e}") from e

        if lookback_bars_int <= 0:
            raise DataPipelineError(f"lookback_bars must be > 0, got {lookback_bars_int}")

        now_utc = datetime.now(timezone.utc)

        request_kwargs: Dict[str, Any] = {
            "symbol_or_symbols": symbol,
            "timeframe": tf_obj,
            "end": now_utc,
            "limit": lookback_bars_int,
        }
        if self._alpaca_feed is not None:
            request_kwargs["feed"] = self._alpaca_feed

        request = StockBarsRequest(**request_kwargs)

        def _call():
            return self.alpaca_client.get_stock_bars(request)

        # bounded retry
        last_err: Optional[Exception] = None
        resp = None
        for attempt in range(3):
            try:
                if self.throttler is not None and hasattr(self.throttler, "execute_sync"):
                    resp = self.throttler.execute_sync(self._alpaca_limit_id, _call)
                else:
                    resp = _call()
                last_err = None
                break
            except Exception as e:
                last_err = e
                delay = self.backoff.next_delay(attempt)
                msg = str(e).lower()
                transient = any(k in msg for k in ("429", "rate", "timeout", "temporar", "connection", "reset"))
                if attempt < 2 and transient:
                    self.logger.warning(
                        "Alpaca bars fetch failed (transient), backing off",
                        extra={"symbol": symbol, "attempt": attempt + 1, "delay_s": round(delay, 2), "error": str(e)},
                    )
                    time.sleep(delay)
                    continue
                break

        if last_err is not None:
            raise DataPipelineError(f"Alpaca bars fetch failed: {last_err}")

        df = self._alpaca_response_to_df(resp, symbol)
        try:
            df = df.sort_index()
        except Exception:
            pass

        self.logger.info(
            "Fetched %s bars for %s",
            len(df),
            symbol,
            extra={"symbol": symbol, "bars": int(len(df)), "timeframe": tf_label, "provider": "alpaca"},
        )
        return df

    def _alpaca_response_to_df(self, resp: Any, symbol: str) -> pd.DataFrame:
        """
        Convert alpaca-py BarsResponse to a DataFrame indexed by timestamp.

        Handles:
        - resp.df MultiIndex (symbol, timestamp)
        - dict-like resp[symbol].df
        """
        if resp is None:
            return pd.DataFrame()

        df = None
        try:
            if hasattr(resp, "df") and resp.df is not None:
                df = resp.df.copy()
            elif hasattr(resp, "__getitem__"):
                df = resp[symbol].df.copy()
        except Exception:
            df = None

        if df is None:
            return pd.DataFrame()

        # Normalize multi-index -> timestamp index
        if isinstance(df.index, pd.MultiIndex):
            names = [n or "" for n in df.index.names]
            # common: level 0 symbol, level 1 timestamp
            try:
                df = df.xs(symbol, level=0)
            except Exception:
                try:
                    df = df.droplevel(0)
                except Exception:
                    pass

        # Clean index to tz-aware UTC
        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
        else:
            # last resort
            df.index = pd.to_datetime(df.index, utc=True)

        # Ensure expected columns exist
        # alpaca uses: open, high, low, close, volume
        return df

    # ============================================================================
    # VALIDATION + TIMEFRAME HELPERS
    # ============================================================================

    def _validate_bars(self, df: pd.DataFrame, symbol: str, tf_label: str) -> bool:
        if df is None or df.empty:
            return False

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            self.logger.warning("Missing required columns for %s", symbol, extra={"symbol": symbol, "cols": list(df.columns)})
            return False

        # index must be datetime
        try:
            pd.to_datetime(df.index)
        except Exception:
            self.logger.warning("Invalid datetime index for %s", symbol, extra={"symbol": symbol})
            return False

        # gap checks are handled in validator (Phase-1), keep here fail-open unless explicitly disallowed
        if not self.allow_gaps and self._has_large_gaps(df, tf_label):
            return False

        return True

    def _normalize_timeframe(self, timeframe: str) -> Tuple[Any, str]:
        """
        Normalize timeframe input into:
        - tf_obj: Alpaca TimeFrame object (for alpaca)
        - tf_label: canonical label string used throughout system ("1Min", "5Min", ...)
        """
        tf = str(timeframe or "").strip()
        # Canonical label
        if tf in ("1Min", "1min", "1m"):
            return TimeFrame(1, TimeFrameUnit.Minute), "1Min"
        if tf in ("5Min", "5min", "5m"):
            return TimeFrame(5, TimeFrameUnit.Minute), "5Min"
        if tf in ("15Min", "15min", "15m"):
            return TimeFrame(15, TimeFrameUnit.Minute), "15Min"
        if tf in ("1Hour", "1hour", "1h", "60Min", "60min", "60m"):
            return TimeFrame(1, TimeFrameUnit.Hour), "1Hour"
        if tf in ("1Day", "1day", "1d", "Day", "day"):
            return TimeFrame(1, TimeFrameUnit.Day), "1Day"

        # parse like "2min", "10m", "3h"
        m = re.match(r"^(\d+)\s*(min|m|hour|hr|h|day|d)$", tf.lower())
        if not m:
            # default safe
            return TimeFrame(1, TimeFrameUnit.Minute), "1Min"

        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("min", "m"):
            return TimeFrame(n, TimeFrameUnit.Minute), f"{n}Min"
        if unit in ("hour", "hr", "h"):
            return TimeFrame(n, TimeFrameUnit.Hour), f"{n}Hour"
        if unit in ("day", "d"):
            return TimeFrame(n, TimeFrameUnit.Day), f"{n}Day"

        return TimeFrame(1, TimeFrameUnit.Minute), "1Min"

    def _safe_last_timestamp(self, df: pd.DataFrame) -> datetime:
        idx_last = df.index[-1]
        if isinstance(idx_last, tuple):
            idx_last = idx_last[-1]
        ts = pd.Timestamp(idx_last)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime()

    def _timeframe_to_seconds(self, tf_label: str) -> int:
        tf = str(tf_label or "").strip().lower()
        if tf.endswith("min"):
            try:
                n = int(tf[:-3] or "1")
            except Exception:
                n = 1
            return n * 60
        if tf.endswith("hour"):
            try:
                n = int(tf[:-4] or "1")
            except Exception:
                n = 1
            return n * 3600
        if tf.endswith("day"):
            try:
                n = int(tf[:-3] or "1")
            except Exception:
                n = 1
            return n * 86400
        # default
        return 60

    def _drop_incomplete_last_bar(self, df: pd.DataFrame, tf_label: str) -> pd.DataFrame:
        """
        Anti-lookahead: drop last bar if it's still "in progress".

        Completeness rule:
          now_utc >= last_ts + timeframe_duration
        """
        if df is None or df.empty:
            return df

        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df = df.copy()
                df.index = pd.to_datetime(df.index, utc=True)
            except Exception:
                return df

        if len(df) <= 1:
            # don’t drop to empty (Phase-1: keep running)
            return df

        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")

        dur = timedelta(seconds=self._timeframe_to_seconds(tf_label))
        now = datetime.now(timezone.utc)

        # If the bar hasn't had time to finish, drop it
        if now < (last_ts.to_pydatetime() + dur):
            return df.iloc[:-1]

        return df

    def _has_large_gaps(self, df: pd.DataFrame, tf_label: str) -> bool:
        if df is None or df.empty or len(df) < 3:
            return False

        expected = pd.Timedelta(seconds=self._timeframe_to_seconds(tf_label))
        idx = pd.to_datetime(df.index, utc=True)
        diffs = pd.Series(idx).diff().dropna()
        if diffs.empty:
            return False

        max_allowed = expected * (1 + float(self.gap_tolerance_pct) / 100.0)
        return bool((diffs > max_allowed).any())

    # ============================================================================
    # CACHE
    # ============================================================================

    def _get_cached(self, key: str) -> Optional[pd.DataFrame]:
        if not self._cache_enabled:
            return None

        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            age = (datetime.now(timezone.utc) - entry.timestamp).total_seconds()
            if age > float(self.cache_ttl.total_seconds()):
                # Expired
                self._cache.pop(key, None)
                return None

            return entry.data

    def _set_cached(self, key: str, df: pd.DataFrame, provider: DataProvider) -> None:
        if not self._cache_enabled:
            return

        if df is None or df.empty:
            return

        with self._cache_lock:
            self._cache[key] = CacheEntry(
                data=df,
                timestamp=datetime.now(timezone.utc),
                provider=provider,
            )

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    # ============================================================================
    # POLICY HELPERS
    # ============================================================================

    def _stale_allowed(self) -> bool:
        """
        Allow stale bars in PAPER mode when permitted by config.
        Env var MQD_ALLOW_STALE_BARS can hard-override:
        - "0" => force fail-closed
        - "1" => force allow
        - unset => defer to config (self.allow_stale_in_paper)
        """
        paper = os.getenv("PAPER_TRADING", "").strip().lower() in ("1", "true", "yes")
        if not paper:
            return False

        env = os.getenv("MQD_ALLOW_STALE_BARS")
        if env is not None:
            return env.strip().lower() in ("1", "true", "yes")

        return bool(self.allow_stale_in_paper)

    def _select_alpaca_feed(self, feed: Optional[str]) -> Any:
        """
        Select Alpaca feed if available.

        feed:
          - "IEX" or "SIP"
        """
        if DataFeed is None:
            return None

        if not feed:
            # allow ENV override
            feed = os.getenv("ALPACA_DATA_FEED") or os.getenv("APCA_DATA_FEED")

        if not feed:
            return None

        f = str(feed).strip().upper()
        if f == "IEX":
            return DataFeed.IEX
        if f == "SIP":
            return DataFeed.SIP
        return None
