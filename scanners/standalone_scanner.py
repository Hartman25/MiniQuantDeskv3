"""
Standalone Market Scanner (GUI + Discord Alerts)

Consolidated patches:
- Patch 5: Session-aware scanning (skip bars when CLOSED)
- Patch 6: Snapshot movers in CLOSED
- Patch 8: Persistence + A+ tagging + row coloring
- Patch 9: Watchlist export (JSON/CSV)
- Patch 10: Gappers persistence + A+ gappers + session override dropdown + clear-state

Run from repo root:
  python -m scanners.standalone_scanner
"""

from __future__ import annotations

import os
import time
import json
import math
import csv
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Optional, Callable
import re

import requests
import tkinter as tk
from tkinter import ttk

from scanners.universe_builder import UniverseBuilder
import traceback
import sys
import webbrowser


# ----------------------------
# Optional .env loading (standalone-friendly)
# ----------------------------

def _load_env_file(path: str) -> None:
    """
    Minimal .env loader (no external deps).
    Loads KEY=VALUE lines into os.environ if key not already set.
    Supports comments (#) and blank lines.
    """
    try:
        if not path:
            return
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        return


# Load scanner envs in this priority order:
# 1) explicit SCANNER_ENV_PATH
# 2) scanners/.env
# 3) repo root .env.scanner
_load_env_file(os.getenv("SCANNER_ENV_PATH", ""))
_load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
_load_env_file(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.scanner"))


# ----------------------------
# Config helpers
# ----------------------------

# Timezones:
# - Market time is always America/New_York (ET) for US equities.
# - Local timezone is detected from the OS each call (so travel is fine).
#   You can override with LOCAL_TZ=IANA_NAME if you want.
MARKET_TZ_NAME = os.getenv("MARKET_TZ", "America/New_York").strip() or "America/New_York"

def _safe_zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        # On Windows, tzdata may be missing in the venv; try importing it (no-op if installed)
        try:
            import tzdata  # type: ignore  # noqa: F401
        except Exception:
            pass
        try:
            return ZoneInfo(name)
        except Exception:
            # Absolute fallback — keeps the app running even if tz database is missing.
            return ZoneInfo("UTC")

def market_tz() -> ZoneInfo:
    return _safe_zoneinfo(MARKET_TZ_NAME)

def local_tzinfo():
    override = os.getenv("LOCAL_TZ", "").strip()
    if override:
        try:
            return _safe_zoneinfo(override)
        except Exception:
            pass
    try:
        return datetime.now().astimezone().tzinfo
    except Exception:
        return timezone.utc

def _debug_enabled() -> bool:
    return os.getenv("DEBUG_LOG", "0").strip() == "1"

def _dlog(msg: str) -> None:
    if _debug_enabled():
        print(f"[DEBUG] {msg}")

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(float(v))
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default


# ----------------------------
# Default universes (used only before UniverseBuilder injects real ones)
# ----------------------------

DEFAULT_NORMAL_UNIVERSE = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD", "GOOGL"
]

DEFAULT_PENNY_UNIVERSE = [
    "SNDL", "NOK", "BB", "PLUG", "BNGO", "FCEL"
]


@dataclass(frozen=True)
class ScannerConfig:
    poll_seconds: int = _env_int("SCANNER_POLL_SECONDS", 30)
    bars_limit: int = _env_int("SCANNER_BARS_LIMIT", 120)
    skip_bars_when_closed: int = _env_int("SKIP_BARS_WHEN_CLOSED", 1)

    # Patch 8: A+ tagging thresholds
    aplus_min_persistence: int = _env_int("APLUS_MIN_PERSISTENCE", 3)
    aplus_min_vol_spike: float = _env_float("APLUS_MIN_VOL_SPIKE", 1.5)
    aplus_min_atr_pct: float = _env_float("APLUS_MIN_ATR_PCT", 0.5)
    aplus_max_atr_pct: float = _env_float("APLUS_MAX_ATR_PCT", 6.0)

    # Patch 9: Watchlist export
    export_dir: str = _env_str("SCANNER_EXPORT_DIR", os.path.join("exports", "scanner"))
    export_auto: int = _env_int("SCANNER_EXPORT_AUTO", 0)  # 1=auto export when A+ exists or gappers exist
    export_write_json: int = _env_int("SCANNER_EXPORT_JSON", 1)
    export_write_csv: int = _env_int("SCANNER_EXPORT_CSV", 1)
    export_top_n: int = _env_int("SCANNER_EXPORT_TOP_N", 25)

    # These get overwritten at runtime by UniverseBuilder
    normal_universe: Tuple[str, ...] = tuple(DEFAULT_NORMAL_UNIVERSE)
    penny_universe: Tuple[str, ...] = tuple(DEFAULT_PENNY_UNIVERSE)

    # Normal filters (applied after scoring)
    normal_min_price: float = _env_float("NORMAL_MIN_PRICE", 5.0)
    normal_max_price: float = _env_float("NORMAL_MAX_PRICE", 500.0)
    normal_min_bar_vol: int = _env_int("NORMAL_MIN_AVG_1M_VOL", 2_000)
    normal_min_atr_pct: float = _env_float("NORMAL_MIN_ATR_PCT", 0.20)
    normal_min_score: float = _env_float("NORMAL_MIN_SCORE", 3.0)

    # Penny filters (scanner-only)
    penny_min_price: float = _env_float("PENNY_MIN_PRICE", 1.0)
    penny_max_price: float = _env_float("PENNY_MAX_PRICE", 5.0)
    penny_min_bar_vol: int = _env_int("PENNY_MIN_AVG_1M_VOL", 10_000)
    penny_min_atr_pct: float = _env_float("PENNY_MIN_ATR_PCT", 0.40)
    penny_min_score: float = _env_float("PENNY_MIN_SCORE", 3.5)

    # Gappers (PRE/POST) thresholds
    gappers_top_n: int = _env_int("GAPPERS_TOP_N", 50)
    pm_gap_min_pct: float = _env_float("PM_GAP_MIN_PCT", 3.0)
    pm_min_volume: int = _env_int("PM_MIN_VOLUME", 50_000)

    # Patch 10: A+ gappers thresholds
    gapplus_min_persistence: int = _env_int("GAPPLUS_MIN_PERSISTENCE", 3)
    gapplus_min_gap_pct: float = _env_float("GAPPLUS_MIN_GAP_PCT", 6.0)
    gapplus_min_volume: int = _env_int("GAPPLUS_MIN_VOLUME", 200_000)
    gapplus_min_price: float = _env_float("GAPPLUS_MIN_PRICE", 1.0)
    gapplus_max_price: float = _env_float("GAPPLUS_MAX_PRICE", 200.0)

    # Batch rotation / rate-limit control
    scan_batch_size: int = _env_int("SCAN_BATCH_SIZE", 300)
    prefilter_top_k: int = _env_int("PREFILTER_TOP_K", 60)
    max_bars_symbols_per_tick: int = _env_int("MAX_BARS_SYMBOLS", 60)

    # Snapshot movers (used when bars are skipped, e.g., CLOSED)
    show_snapshot_movers_when_closed: int = _env_int("SHOW_SNAPSHOT_MOVERS_WHEN_CLOSED", 1)
    snap_min_move_pct_normal: float = _env_float("SNAP_MIN_MOVE_PCT_NORMAL", 2.0)
    snap_min_move_pct_penny: float = _env_float("SNAP_MIN_MOVE_PCT_PENNY", 4.0)
    snap_min_volume_normal: int = _env_int("SNAP_MIN_VOLUME_NORMAL", 50_000)
    snap_min_volume_penny: int = _env_int("SNAP_MIN_VOLUME_PENNY", 150_000)


@dataclass
class Candidate:
    symbol: str
    last: float
    vwap: float
    ema9: float
    ema20: float
    ema50: float
    atr_pct: float
    vol_spike: float
    score: float
    session: str
    universe: str
    notes: str
    news_headline: str = ""
    news_source: str = ""
    news_age_min: int = 0
    news_url: str = ""
    catalyst: str = ""
    risk_flag: str = ""
    ready: int = 0
    ready_reason: str = ""

@dataclass
class Gapper:
    symbol: str
    last: float
    prev_close: float
    gap_pct: float
    session_vol: int
    score: float
    session: str
    universe: str
    notes: str
    news_headline: str = ""
    news_source: str = ""
    news_age_min: int = 0
    news_url: str = ""
    catalyst: str = ""
    risk_flag: str = ""
    ready: int = 0
    ready_reason: str = ""


@dataclass
class ScanStats:
    mode: str
    normal_batch: int
    penny_batch: int
    normal_pick: int
    penny_pick: int
    normal_bars: int
    penny_bars: int
    normal_hits: int
    penny_hits: int
    gappers_hits: int
    backoff_s: float



# ----------------------------
# Patch 18/21/22: Signal export (JSONL) for bot integration + Phase-4 ML outcomes
# ----------------------------

class SignalExporter:
    """Append-only JSONL exporter for scanner signals.

    Writes one line per candidate/gapper to a JSONL file:
      exports/scanner_signals.jsonl   (default)

    This is designed to be consumed by scanners/signal_consumer.py and later joined
    with outcomes logged via scanners/signal_outcomes.py.
    """

    def __init__(self, enabled: bool, path: str, top_n: int = 100):
        self.enabled = enabled
        self.path = Path(path)
        self.top_n = max(1, int(top_n))
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def from_env() -> "SignalExporter":
        enabled = os.getenv("SCANNER_EXPORT_SIGNALS", "1").strip() == "1"
        path = os.getenv("SCANNER_SIGNALS_PATH", os.path.join("exports", "scanner_signals.jsonl")).strip()             or os.path.join("exports", "scanner_signals.jsonl")
        top_n = _env_int("SCANNER_EXPORT_TOP_N", 100)
        return SignalExporter(enabled=enabled, path=path, top_n=top_n)

    def _write(self, rec: Dict[str, object]) -> None:
        if not self.enabled:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            # Never crash the UI/engine because export failed.
            _dlog("signal export failed:\n" + traceback.format_exc())

    def export_scan(
        self,
        ts_et: datetime,
        session: str,
        mode: str,
        normal: List[Candidate],
        penny: List[Candidate],
        gappers: List[Gapper],
    ) -> None:
        if not self.enabled:
            return

        ts_utc = ts_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        def cand_to_rec(c: Candidate) -> Dict[str, object]:
            # notes carries A+/reasoning + may include headline; keep separate headline/url when available.
            headline = None
            news_url = None
            source = None
            # If notes contains a "NEWS:" token (Patch 12), we keep notes as-is; headline/url live elsewhere.
            # The GUI row dict (later patches) may attach headline/news_url; we keep best-effort here.
            return {
                "ts_utc": ts_utc,
                "symbol": c.symbol,
                "mode": mode,
                "session": session,
                "universe": c.universe,
                "score": round(float(c.score), 4) if c.score is not None else None,
                "last": c.last,
                "vwap": c.vwap,
                "ema9": c.ema9,
                "ema20": c.ema20,
                "ema50": c.ema50,
                "atr_pct": c.atr_pct,
                "vol_spike": c.vol_spike,
                "persistence": None,  # persistence is tracked internally; UI exports the value separately when available
                "catalyst": None,
                "risk_flags": [],
                "ready": None,
                "ready_reason": None,
                "headline": headline,
                "news_url": news_url,
                "source": source,
                "notes": c.notes,
            }

        def gap_to_rec(g: Gapper) -> Dict[str, object]:
            return {
                "ts_utc": ts_utc,
                "symbol": g.symbol,
                "mode": mode,
                "session": session,
                "universe": g.universe,
                "score": round(float(g.score), 4) if g.score is not None else None,
                "gap_pct": g.gap_pct,
                "session_vol": g.session_vol,
                "last": g.last,
                "headline": None,
                "news_url": None,
                "source": None,
                "notes": g.notes,
            }

        # Keep file lean: export only top-N by score per bucket
        normal_sorted = sorted(normal, key=lambda x: x.score, reverse=True)[: self.top_n]
        penny_sorted = sorted(penny, key=lambda x: x.score, reverse=True)[: self.top_n]
        gappers_sorted = sorted(gappers, key=lambda x: x.score, reverse=True)[: self.top_n]

        for c in normal_sorted:
            self._write(cand_to_rec(c))
        for c in penny_sorted:
            self._write(cand_to_rec(c))
        for g in gappers_sorted:
            self._write(gap_to_rec(g))


# ----------------------------
# Helpers: time/session
# ----------------------------

def now_et() -> datetime:
    return datetime.now(tz=market_tz())

def now_local() -> datetime:
    # Uses OS timezone (changes automatically when you travel)
    return datetime.now(tz=local_tzinfo())

def session_name(ts_et: datetime) -> str:
    t = ts_et.time()
    if t >= datetime(2000, 1, 1, 4, 0).time() and t < datetime(2000, 1, 1, 9, 30).time():
        return "PRE"
    if t >= datetime(2000, 1, 1, 9, 30).time() and t < datetime(2000, 1, 1, 16, 0).time():
        return "REG"
    if t >= datetime(2000, 1, 1, 16, 0).time() and t < datetime(2000, 1, 1, 20, 0).time():
        return "POST"
    return "CLOSED"


class RotatingBatches:
    def __init__(self, symbols: List[str], batch_size: int):
        self.symbols = symbols[:]
        self.batch_size = max(1, batch_size)
        self.i = 0

    def next_batch(self) -> List[str]:
        if not self.symbols:
            return []
        n = len(self.symbols)
        bs = self.batch_size

        start = self.i
        end = start + bs

        if end <= n:
            batch = self.symbols[start:end]
        else:
            batch = self.symbols[start:n] + self.symbols[0:(end % n)]

        self.i = end % n
        return batch

    def update(self, symbols: List[str]):
        self.symbols = symbols[:]
        self.i = 0


# ----------------------------
# Indicators
# ----------------------------

def ema(series: List[float], period: int) -> Optional[float]:
    if len(series) < period or period <= 0:
        return None
    k = 2 / (period + 1)
    e = series[0]
    for x in series[1:]:
        e = x * k + e * (1 - k)
    return e

def atr(high: List[float], low: List[float], close: List[float], period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    trs = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period

def vwap(high: List[float], low: List[float], close: List[float], volume: List[float]) -> Optional[float]:
    if not close or len(close) != len(volume):
        return None
    tpv = 0.0
    vv = 0.0
    for h, l, c, v in zip(high, low, close, volume):
        tp = (h + l + c) / 3.0
        tpv += tp * v
        vv += v
    if vv <= 0:
        return None
    return tpv / vv


# ----------------------------
# Alpaca REST Client
# ----------------------------

class AlpacaDataREST:
    def __init__(self):
        self.base_url = os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")
        self.key = os.getenv("ALPACA_API_KEY", "").strip()
        self.secret = os.getenv("ALPACA_API_SECRET", "").strip()
        if not self.key or not self.secret:
            raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_API_SECRET in environment.")

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret,
        }

    def get_bars_1m(self, symbols: List[str], start_utc: datetime, end_utc: datetime, limit: int = 120) -> Dict[str, List[dict]]:
        if not symbols:
            return {}
        url = f"{self.base_url}/v2/stocks/bars"
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": start_utc.isoformat().replace("+00:00", "Z"),
            "end": end_utc.isoformat().replace("+00:00", "Z"),
            "limit": str(limit),
            "adjustment": "raw",
            "feed": "iex",
        }
        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca bars error {r.status_code}: {r.text[:300]}")
        data = r.json() or {}
        out: Dict[str, List[dict]] = {}
        bars = data.get("bars", {}) or {}
        for sym, arr in bars.items():
            out[sym] = arr or []
        for sym in symbols:
            out.setdefault(sym, [])
        return out

    def get_snapshots(self, symbols: List[str]) -> Dict[str, dict]:
        if not symbols:
            return {}

        base = self.base_url.rstrip("/")
        if base.endswith("/v2"):
            base = base[:-3]

        url = f"{base}/v2/stocks/snapshots"
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        r = requests.get(url, headers=self._headers(), params=params, timeout=25)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca snapshots error {r.status_code}: {r.text[:300]}")
        data = r.json() or {}

        snaps = data.get("snapshots")
        if isinstance(snaps, dict):
            return snaps

        if isinstance(data, dict):
            batch_set = set(symbols)
            return {k: v for k, v in data.items() if k in batch_set and isinstance(v, dict)}

        return {}



# ----------------------------
# Alpaca News REST (Patch 11)
# ----------------------------

@dataclass
class NewsHit:
    symbol: str
    headline: str
    source: str
    created_at: datetime
    url: str
    catalyst: str = ""
    risk_flag: str = ""

class AlpacaNewsREST:
    """
    Fetches latest news from Alpaca News API (v1beta1).
    Docs: https://docs.alpaca.markets/reference/news-3
    Note: Some Alpaca accounts/plans may not have news access; this client fails soft.
    """
    def __init__(self):
        self.base_url = os.getenv("ALPACA_NEWS_BASE_URL", os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")).rstrip("/")
        self.key = os.getenv("ALPACA_API_KEY", "").strip()
        self.secret = os.getenv("ALPACA_API_SECRET", "").strip()
        self.lookback_hours = _env_int("NEWS_LOOKBACK_HOURS", 24)
        self.max_per_symbol = max(1, _env_int("NEWS_MAX_PER_SYMBOL", 3))
        self.ttl_seconds = max(30, _env_int("NEWS_TTL_SECONDS", 600))
        self.headline_max = max(40, _env_int("NEWS_HEADLINE_MAX_CHARS", 90))

        self._disabled = False
        # per-symbol cache: sym -> (fetched_ts, NewsHit|None)
        self._cache: Dict[str, Tuple[float, Optional[NewsHit]]] = {}

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret,
        }

    def _truncate(self, s: str) -> str:
        s = (s or "").strip()
        if len(s) <= self.headline_max:
            return s
        return s[: self.headline_max - 1].rstrip() + "…"

    def _age_minutes(self, created_at: datetime) -> int:
        try:
            now = datetime.now(timezone.utc)
            dt = created_at.astimezone(timezone.utc)
            return max(0, int((now - dt).total_seconds() // 60))
        except Exception:
            return 0

    def _parse_created_at(self, s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            # Alpaca returns ISO8601; normalize Z
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def get_latest_for_symbol(self, symbol: str) -> Optional[NewsHit]:
        if self._disabled:
            return None
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return None

        now_ts = time.time()
        cached = self._cache.get(symbol)
        if cached and (now_ts - cached[0] <= self.ttl_seconds):
            return cached[1]

        start = (datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).isoformat().replace("+00:00", "Z")
        url = f"{self.base_url}/v1beta1/news"
        params = {
            "symbols": symbol,
            "start": start,
            "limit": str(self.max_per_symbol),
            "sort": "desc",
        }

        try:
            r = requests.get(url, headers=self._headers(), params=params, timeout=15)
            if r.status_code in (401, 403):
                # No entitlement; disable to prevent hammering
                self._disabled = True
                self._cache[symbol] = (now_ts, None)
                return None
            if r.status_code != 200:
                # don't disable permanently; just cache None briefly
                self._cache[symbol] = (now_ts, None)
                return None

            data = r.json() or {}
            news = data.get("news") or []
            best: Optional[NewsHit] = None
            for item in news:
                if not isinstance(item, dict):
                    continue
                headline = (item.get("headline") or "").strip()
                if not headline:
                    continue
                created_at = self._parse_created_at(item.get("created_at") or item.get("createdAt") or "")
                if created_at is None:
                    continue
                source = (item.get("source") or "").strip()
                # try common url fields
                url_field = item.get("url") or item.get("link") or ""
                url_field = (url_field or "").strip()
                best = NewsHit(
                    symbol=symbol,
                    headline=self._truncate(headline),
                    source=source,
                    created_at=created_at,
                    url=url_field,
                )
                break

            self._cache[symbol] = (now_ts, best)
            return best
        except Exception:
            self._cache[symbol] = (now_ts, None)
            return None

    def get_latest_for_symbols(self, symbols: List[str]) -> Dict[str, Optional[NewsHit]]:
        out: Dict[str, Optional[NewsHit]] = {}
        for s in symbols:
            out[s] = self.get_latest_for_symbol(s)
        return out



# ----------------------------
# Catalyst classification + Multi-source News (Patch 12)
# ----------------------------

class CatalystClassifier:
    """Fast, rule-based headline classifier. Returns (catalyst, risk_flag)."""
    # Order matters: first match wins for risk
    RISK_PATTERNS = [
        ("DILUTION", re.compile(r"\b(offering|registered direct|atm\b|at-the-market|s-3\b|s3\b|shelf|dilution|dilutive|warrant|warrants|convertible|notes? offering)\b", re.I)),
        ("REVERSE_SPLIT", re.compile(r"\b(reverse split|reverse-split|1-for-\d+|\d+-for-1)\b", re.I)),
        ("HALT", re.compile(r"\b(trading halt|halted|volatility halt)\b", re.I)),
    ]
    CATALYST_PATTERNS = [
        ("EARNINGS", re.compile(r"\b(earnings|eps|revenue|guidance|q[1-4]|quarter|beats?|miss(?:es|ed)?)\b", re.I)),
        ("FDA", re.compile(r"\b(fda|phase\s*[1-3]|clinical|trial|pdufa|drug|biotech)\b", re.I)),
        ("M&A", re.compile(r"\b(acquire|acquisition|merger|buyout|takeover)\b", re.I)),
        ("CONTRACT", re.compile(r"\b(contract|award|order|partnership|collaboration|agreement)\b", re.I)),
        ("ANALYST", re.compile(r"\b(upgrade|downgrade|initiates?|price target|pt\b|analyst)\b", re.I)),
        ("MACRO", re.compile(r"\b(cpi|jobs report|fed|rates?|inflation)\b", re.I)),
    ]

    def classify(self, headline: str) -> Tuple[str, str]:
        h = (headline or "").strip()
        if not h:
            return ("", "")
        risk = ""
        for name, rx in self.RISK_PATTERNS:
            if rx.search(h):
                risk = name
                break
        cat = "UNKNOWN"
        for name, rx in self.CATALYST_PATTERNS:
            if rx.search(h):
                cat = name
                break
        return (cat, risk)


class FinnhubNewsREST:
    """Fetches latest company news from Finnhub."""
    def __init__(self, classifier: Optional[CatalystClassifier] = None):
        self.base_url = os.getenv("FINNHUB_BASE_URL", "https://finnhub.io").rstrip("/")
        self.key = os.getenv("FINNHUB_API_KEY", "").strip()
        self.lookback_h = _env_int("NEWS_LOOKBACK_HOURS", 24)
        self.ttl_s = _env_int("NEWS_TTL_SECONDS", 600)
        self.headline_max = _env_int("NEWS_HEADLINE_MAX_CHARS", 90)
        self._classifier = classifier or CatalystClassifier()
        self._cache: Dict[str, Tuple[float, Optional[NewsHit]]] = {}

    def _truncate(self, s: str) -> str:
        s = (s or "").strip().replace("\n", " ")
        if len(s) <= self.headline_max:
            return s
        return s[: self.headline_max - 1].rstrip() + "…"

    def _age_minutes(self, created_at: datetime) -> int:
        try:
            now = datetime.now(timezone.utc)
            dt = created_at.astimezone(timezone.utc)
            return max(0, int((now - dt).total_seconds() // 60))
        except Exception:
            return 0

    def _get(self, url: str, params: Dict[str, str]) -> Optional[dict]:
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def get_latest_for_symbol(self, symbol: str) -> Optional[NewsHit]:
        symbol = (symbol or "").strip().upper()
        if not symbol or not self.key:
            return None

        now_ts = time.time()
        cached = self._cache.get(symbol)
        if cached and cached[0] > now_ts:
            return cached[1]

        # Finnhub expects from/to YYYY-MM-DD
        to_dt = datetime.now(timezone.utc).date()
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=self.lookback_h)).date()

        url = f"{self.base_url}/api/v1/company-news"
        params = {
            "symbol": symbol,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "token": self.key,
        }
        data = self._get(url, params)
        hit: Optional[NewsHit] = None
        if isinstance(data, list) and data:
            # choose newest by datetime
            best = None
            best_ts = -1
            for item in data[:25]:
                try:
                    ts = int(item.get("datetime") or 0)
                    if ts > best_ts and item.get("headline"):
                        best_ts = ts
                        best = item
                except Exception:
                    continue
            if best and best_ts > 0:
                created = datetime.fromtimestamp(best_ts, tz=timezone.utc)
                headline = self._truncate(best.get("headline", ""))
                source = (best.get("source") or "Finnhub").strip()
                url2 = (best.get("url") or "").strip()
                cat, risk = self._classifier.classify(headline)
                hit = NewsHit(symbol=symbol, headline=headline, source=source, created_at=created, url=url2, catalyst=cat, risk_flag=risk)

        self._cache[symbol] = (now_ts + self.ttl_s, hit)
        return hit

    def get_latest_for_symbols(self, symbols: List[str]) -> Dict[str, Optional[NewsHit]]:
        out: Dict[str, Optional[NewsHit]] = {}
        for s in symbols:
            out[s] = self.get_latest_for_symbol(s)
        return out


class FMPNewsREST:
    """Optional: fetches latest stock_news from Financial Modeling Prep for extra dilution detection."""
    def __init__(self, classifier: Optional[CatalystClassifier] = None):
        self.base_url = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")
        self.key = os.getenv("FMP_API_KEY", "").strip()
        self.limit = _env_int("FMP_NEWS_LIMIT", 5)
        self.ttl_s = _env_int("NEWS_TTL_SECONDS", 600)
        self.headline_max = _env_int("NEWS_HEADLINE_MAX_CHARS", 90)
        self._classifier = classifier or CatalystClassifier()
        self._cache: Dict[str, Tuple[float, Optional[NewsHit]]] = {}

    def _truncate(self, s: str) -> str:
        s = (s or "").strip().replace("\n", " ")
        if len(s) <= self.headline_max:
            return s
        return s[: self.headline_max - 1].rstrip() + "…"

    def get_latest_for_symbol(self, symbol: str) -> Optional[NewsHit]:
        symbol = (symbol or "").strip().upper()
        if not symbol or not self.key:
            return None

        now_ts = time.time()
        cached = self._cache.get(symbol)
        if cached and cached[0] > now_ts:
            return cached[1]

        url = f"{self.base_url}/api/v3/stock_news"
        params = {"tickers": symbol, "limit": str(self.limit), "apikey": self.key}
        hit: Optional[NewsHit] = None
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    item = data[0]
                    headline = self._truncate(item.get("title") or item.get("text") or "")
                    if headline:
                        # FMP dates are strings; best-effort parse
                        dt_s = item.get("publishedDate") or item.get("date") or ""
                        created = None
                        try:
                            created = datetime.fromisoformat(dt_s.replace("Z", "+00:00"))
                        except Exception:
                            created = datetime.now(timezone.utc)
                        source = (item.get("site") or item.get("source") or "FMP").strip()
                        url2 = (item.get("url") or "").strip()
                        cat, risk = self._classifier.classify(headline)
                        hit = NewsHit(symbol=symbol, headline=headline, source=source, created_at=created, url=url2, catalyst=cat, risk_flag=risk)
        except Exception:
            hit = None

        self._cache[symbol] = (now_ts + self.ttl_s, hit)
        return hit

    def get_latest_for_symbols(self, symbols: List[str]) -> Dict[str, Optional[NewsHit]]:
        out: Dict[str, Optional[NewsHit]] = {}
        for s in symbols:
            out[s] = self.get_latest_for_symbol(s)
        return out


class NewsService:
    """Primary news provider selection with soft fallback."""
    def __init__(self):
        self.classifier = CatalystClassifier()
        self.provider = os.getenv("NEWS_PROVIDER", "AUTO").strip().upper()
        self.alpaca = AlpacaNewsREST()
        self.finnhub = FinnhubNewsREST(self.classifier)
        self.fmp = FMPNewsREST(self.classifier)
        # Enable FMP as a dilution cross-check on pennies if key is present
        self.enable_fmp = _env_int("ENABLE_FMP_NEWS", 1) == 1 and bool(self.fmp.key)

    def _age_minutes(self, created_at: datetime) -> int:
        # keep API compatibility with older code
        try:
            now = datetime.now(timezone.utc)
            dt = created_at.astimezone(timezone.utc)
            return max(0, int((now - dt).total_seconds() // 60))
        except Exception:
            return 0

    def get_latest_for_symbol(self, symbol: str) -> Optional[NewsHit]:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return None

        # provider routing
        if self.provider == "FINNHUB":
            hit = self.finnhub.get_latest_for_symbol(symbol)
        elif self.provider == "ALPACA":
            hit = self.alpaca.get_latest_for_symbol(symbol)
        else:
            # AUTO: prefer Finnhub if key set; else Alpaca
            hit = self.finnhub.get_latest_for_symbol(symbol) or self.alpaca.get_latest_for_symbol(symbol)

        # optional FMP cross-check for dilution risk; only override risk if it finds DILUTION
        if self.enable_fmp:
            fmp_hit = self.fmp.get_latest_for_symbol(symbol)
            if fmp_hit and fmp_hit.risk_flag == "DILUTION":
                if hit is None or hit.risk_flag != "DILUTION":
                    hit = fmp_hit
        return hit

    def get_latest_for_symbols(self, symbols: List[str]) -> Dict[str, Optional[NewsHit]]:
        out: Dict[str, Optional[NewsHit]] = {}
        for s in symbols:
            out[s] = self.get_latest_for_symbol(s)
        return out


# ----------------------------
# Discord alerts
# ----------------------------

class DiscordNotifier:
    def __init__(self):
        self.webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    def enabled(self) -> bool:
        return bool(self.webhook)

    def send(self, title: str, lines: List[str]) -> None:
        if not self.webhook or not lines:
            return
        content = f"**{title}**\n" + "\n".join(lines[:15])
        if len(content) > 1800:
            content = content[:1800] + "…"
        try:
            requests.post(self.webhook, json={"content": content}, timeout=10)
        except Exception:
            pass


# ----------------------------
# Scoring
# ----------------------------

def compute_candidate(symbol: str, bars: List[dict], universe: str, sess: str) -> Optional[Candidate]:
    if len(bars) < 60:
        return None

    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    v = [b["v"] for b in bars]

    last = float(c[-1])
    vwap_val = vwap(h, l, c, v)
    e9 = ema(c[-60:], 9)
    e20 = ema(c[-80:], 20)
    e50 = ema(c[-120:], 50)
    atr_val = atr(h[-80:], l[-80:], c[-80:], 14)

    if None in (vwap_val, e9, e20, e50, atr_val):
        return None

    atr_pct = (atr_val / last) * 100.0 if last > 0 else 0.0

    v5 = sum(v[-5:])
    v20 = sum(v[-25:-5]) if len(v) >= 25 else sum(v[:-5])
    vol_spike = (v5 / max(v20, 1)) * (20 / 5)

    above_vwap = last > vwap_val
    ema_stack = e9 > e20 > e50

    recent_lows = min(c[-10:])
    touched_pullback_zone = (recent_lows <= e9 * 1.002) or (recent_lows <= e20 * 1.002)
    green_now = c[-1] >= o[-1]

    score = 0.0
    notes = []

    if above_vwap:
        score += 1.2
        notes.append("AboveVWAP")
    if ema_stack:
        score += 1.2
        notes.append("EMA9>20>50")
    if vol_spike >= 1.5:
        score += 1.0
        notes.append(f"VolSpike {vol_spike:.2f}x")
    if touched_pullback_zone and green_now:
        score += 0.8
        notes.append("Pullback+Green")
    if atr_pct >= 0.3:
        score += 0.6
        notes.append(f"ATR% {atr_pct:.2f}")

    return Candidate(
        symbol=symbol,
        last=last,
        vwap=float(vwap_val),
        ema9=float(e9),
        ema20=float(e20),
        ema50=float(e50),
        atr_pct=float(atr_pct),
        vol_spike=float(vol_spike),
        score=float(score),
        session=sess,
        universe=universe,
        notes=", ".join(notes) if notes else ""
    )


# ----------------------------
# Scanner Engine
# ----------------------------

class ScannerEngine:
    def __init__(self, cfg: ScannerConfig, ui_callback: Callable[..., None]):
        self.cfg = cfg
        self.ui_callback = ui_callback
        self.client = AlpacaDataREST()
        self.news = NewsService()

        # Dynamic thresholds by session (PRE/REG/POST)
        # These are multiplicative relax factors applied to thresholds
        # e.g., 0.85 means "reduce required thresholds by 15%" (easier to qualify)
        self.session_pre_relax = _env_float("SESSION_PRE_RELAX", 0.85)
        self.session_post_relax = _env_float("SESSION_POST_RELAX", 0.90)
        self.session_reg_relax = _env_float("SESSION_REG_RELAX", 1.00)


        self._normal_batches = RotatingBatches(list(cfg.normal_universe), cfg.scan_batch_size)
        self._penny_batches = RotatingBatches(list(cfg.penny_universe), cfg.scan_batch_size)

        self.discord = DiscordNotifier()
        self.exporter = SignalExporter.from_env()
        # Two-gate universe (optional)
        try:
            from core.universe import get_scanner_adapter
            self.universe_adapter = get_scanner_adapter()
        except Exception:
            self.universe_adapter = None

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Patch 8: persistence tracking (for scored candidates)
        self._persistence: Dict[str, int] = {}   # symbol -> consecutive scans seen (normal/penny)
        # Patch 10: gappers persistence
        self._gapper_persistence: Dict[str, int] = {}  # symbol -> consecutive scans seen (gappers)

        # Patch 10: session override (AUTO/REG/PREPOST/CLOSED)
        self._session_mode: str = "AUTO"

        # alert throttles
        self._last_alerted: Dict[str, float] = {}   # key -> last_score
        self._gappers_state: Dict[str, Gapper] = {} # symbol -> best gapper seen this day
        self._gappers_day_et = now_et().date()      # daily reset guard (ET)

        # rate-limit / backoff
        self._backoff_s = 0.0
        self._backoff_max_s = float(_env_int("SCANNER_BACKOFF_MAX_SECONDS", 300))
        self._backoff_step_s = float(_env_int("SCANNER_BACKOFF_STEP_SECONDS", 15))
        self._last_stats: Optional[ScanStats] = None

        # Patch 9: last outputs (for export)
        self._last_normal: List[Candidate] = []
        self._last_penny: List[Candidate] = []
        self._last_gappers: List[Gapper] = []

        # Session-based relax factors (dynamic thresholds)
        # PRE/POST are often thinner / noisier; relax gates slightly to avoid empty boards.
        self.session_pre_relax = float(os.getenv("SESSION_PRE_RELAX", "0.85"))
        self.session_post_relax = float(os.getenv("SESSION_POST_RELAX", "0.90"))
        self.session_reg_relax = float(os.getenv("SESSION_REG_RELAX", "1.00"))

    def _session_relax_factor(self, sess: str) -> float:
        """Return multiplicative relax factor for thresholds based on session."""
        s = (sess or "").upper()
        if s == "PRE":
            return self.session_pre_relax
        if s == "POST":
            return self.session_post_relax
        return self.session_reg_relax

    def set_session_mode(self, mode: str) -> None:
        mode = (mode or "").strip().upper()
        if mode not in ("AUTO", "REG", "PREPOST", "CLOSED"):
            mode = "AUTO"
        self._session_mode = mode

    def clear_state(self) -> None:
        # Patch 10: one-click reset
        self._persistence.clear()
        self._gapper_persistence.clear()
        self._gappers_state.clear()
        self._last_alerted.clear()
        self._gappers_day_et = now_et().date()
        self._backoff_s = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.scan_once()
                self._backoff_s = 0.0
            except Exception as e:
                if self._is_rate_limit_error(e):
                    self._increase_backoff()
                self.ui_callback(error=str(e), stats=self._last_stats)

            elapsed = time.time() - t0
            sleep_s = max(0.5, float(self.cfg.poll_seconds) + float(self._backoff_s) - elapsed)

            if self._last_stats:
                self._last_stats.backoff_s = float(self._backoff_s)
                self.ui_callback(stats=self._last_stats)

            time.sleep(sleep_s)

    def _resolve_session(self, ts: datetime) -> Tuple[str, str]:
        """
        Returns (session, mode_label) where mode_label reflects forced mode.
        """
        if self._session_mode == "AUTO":
            return session_name(ts), "AUTO"
        if self._session_mode == "REG":
            return "REG", "REG"
        if self._session_mode == "PREPOST":
            return "PRE", "PREPOST"
        if self._session_mode == "CLOSED":
            return "CLOSED", "CLOSED"
        return session_name(ts), "AUTO"

    def scan_once(self):
        ts = now_et()
        sess, mode_label = self._resolve_session(ts)

        _dlog(f"scan_once: ts_et={ts.isoformat()} sess={sess} mode={mode_label} poll={self.cfg.poll_seconds}s")

        # daily reset for gappers/alerts
        if ts.date() != self._gappers_day_et:
            self._gappers_day_et = ts.date()
            self._gappers_state.clear()
            self._last_alerted.clear()
            self._gapper_persistence.clear()

        normal_batch = self._normal_batches.next_batch()
        penny_batch = self._penny_batches.next_batch()
        _dlog(f"batches: normal={len(normal_batch)} penny={len(penny_batch)}")

        # ---------------- gappers ----------------
        gappers: List[Gapper] = []
        g_seen_symbols: List[str] = []
        gappers_enabled = (sess in ("PRE", "POST")) or (mode_label == "PREPOST")
        _dlog(f"gappers_enabled={gappers_enabled} (sess={sess}, mode={mode_label})")

        if gappers_enabled:
            g_all: List[Gapper] = []
            for universe, batch in (("NORMAL", normal_batch), ("PENNY", penny_batch)):
                g = self._compute_gappers_from_snapshots(batch, universe=universe, sess=sess)
                g_all.extend(g)

                for item in g:
                    prev = self._gappers_state.get(item.symbol)
                    if (prev is None) or (item.score > prev.score):
                        self._gappers_state[item.symbol] = item

            g_seen_symbols = [x.symbol for x in g_all]
            self._update_gapper_persistence(g_seen_symbols)

            all_g = list(self._gappers_state.values())
            # annotate with persistence + A+G tags using current persistence
            self._annotate_gappers(all_g)
            all_g.sort(key=lambda x: x.score, reverse=True)
            gappers = all_g[: self.cfg.gappers_top_n]
        _dlog(f"gappers_hits={len(gappers)}")

        # ---------------- bars vs snapshot movers ----------------
        do_bars = not (sess == "CLOSED" and self.cfg.skip_bars_when_closed == 1)
        do_snapshot_movers = (sess == "CLOSED" and self.cfg.show_snapshot_movers_when_closed == 1 and not do_bars)
        _dlog(f"do_bars={do_bars} do_snapshot_movers={do_snapshot_movers}")

        normal_pick: List[str] = []
        penny_pick: List[str] = []
        normal_cands: List[Candidate] = []
        penny_cands: List[Candidate] = []
        normal_bars: Dict[str, List[dict]] = {}
        penny_bars: Dict[str, List[dict]] = {}

        if do_snapshot_movers:
            normal_cands = self._snapshot_movers(normal_batch, universe="NORMAL", sess=sess)
            penny_cands = self._snapshot_movers(penny_batch, universe="PENNY", sess=sess)

        if do_bars:
            normal_pick = self._prefilter_with_snapshots(normal_batch, universe="NORMAL")[: self.cfg.max_bars_symbols_per_tick]
            penny_pick = self._prefilter_with_snapshots(penny_batch, universe="PENNY")[: self.cfg.max_bars_symbols_per_tick]
            _dlog(f"prefilter picks: normal={len(normal_pick)} penny={len(penny_pick)}")

            end_utc = datetime.now(timezone.utc)
            start_utc = end_utc - timedelta(minutes=self.cfg.bars_limit + 10)

            normal_bars = self.client.get_bars_1m(normal_pick, start_utc, end_utc, limit=self.cfg.bars_limit) if normal_pick else {}
            penny_bars = self.client.get_bars_1m(penny_pick, start_utc, end_utc, limit=self.cfg.bars_limit) if penny_pick else {}
            _dlog(f"bars fetched: normal_syms={len(normal_bars)} penny_syms={len(penny_bars)}")

            normal_cands = self._score_universe(normal_bars, "NORMAL", sess)
            penny_cands = self._score_universe(penny_bars, "PENNY", sess)
            _dlog(f"scored: normal_hits={len(normal_cands)} penny_hits={len(penny_cands)}")

        # Patch 8: update persistence based on current scored candidates only (not gappers)
        self._update_persistence([c.symbol for c in normal_cands] + [c.symbol for c in penny_cands])
        self._annotate_candidates(normal_cands)
        self._annotate_candidates(penny_cands)

        # Patch 11/12: attach news + catalysts (soft-fail)
        try:
            self._attach_news_to_rows(normal_cands, penny_cands, gappers)
        except Exception:
            pass

        # Patch 17: readiness tagging
        self._tag_readiness(normal_cands, penny_cands, gappers)

        # Patch 9: stash last outputs for export
        self._last_normal = normal_cands[:]
        self._last_penny = penny_cands[:]
        self._last_gappers = gappers[:]

        # Patch 9: auto-export
        if self.cfg.export_auto == 1:
            if self._has_exportworthy(normal_cands, penny_cands, gappers):
                self.export_watchlist()

        # telemetry
        self._last_stats = ScanStats(
            mode=mode_label,
            normal_batch=len(normal_batch),
            penny_batch=len(penny_batch),
            normal_pick=len(normal_pick),
            penny_pick=len(penny_pick),
            normal_bars=len(normal_bars) if isinstance(normal_bars, dict) else 0,
            penny_bars=len(penny_bars) if isinstance(penny_bars, dict) else 0,
            normal_hits=len(normal_cands),
            penny_hits=len(penny_cands),
            gappers_hits=len(gappers),
            backoff_s=float(self._backoff_s),
        )

        self.ui_callback(
            timestamp=ts.strftime("%Y-%m-%d %H:%M:%S ET"),
            session=sess,
            mode=mode_label,
            normal=normal_cands,
            penny=penny_cands,
            gappers=gappers,
            stats=self._last_stats,
            error=None
        )

        if self.discord.enabled():
            self._maybe_alert(sess, normal_cands, "NORMAL")
            self._maybe_alert(sess, penny_cands, "PENNY")
            if gappers_enabled and gappers:
                self._maybe_alert_gappers(sess, gappers)


        # Patch 18/21/22: export signals (never raises)
        self.exporter.export_scan(ts_et=ts, session=sess, mode=mode_label, normal=normal_cands, penny=penny_cands, gappers=gappers)

    def _score_universe(self, bars_map: Dict[str, List[dict]], universe: str, sess: str) -> List[Candidate]:
        out: List[Candidate] = []
        for sym, bars in bars_map.items():
            cand = compute_candidate(sym, bars, universe, sess)
            if not cand:
                continue

            if universe == "NORMAL":
                if not (self.cfg.normal_min_price <= cand.last <= self.cfg.normal_max_price):
                    continue
                if cand.atr_pct < self.cfg.normal_min_atr_pct:
                    continue
                avg_v = sum([b["v"] for b in bars[-30:]]) / 30.0
                if avg_v < self.cfg.normal_min_bar_vol:
                    continue
                if cand.score < self.cfg.normal_min_score:
                    continue
            else:
                if not (self.cfg.penny_min_price <= cand.last <= self.cfg.penny_max_price):
                    continue
                if cand.atr_pct < self.cfg.penny_min_atr_pct:
                    continue
                avg_v = sum([b["v"] for b in bars[-30:]]) / 30.0
                if avg_v < self.cfg.penny_min_bar_vol:
                    continue
                if cand.score < self.cfg.penny_min_score:
                    continue

            out.append(cand)

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:25]

    def _maybe_alert(self, sess: str, cands: List[Candidate], universe: str):
        lines = []
        alerted = False
        for c in cands[:5]:
            key = f"{universe}:{c.symbol}"
            prev = self._last_alerted.get(key, 0.0)
            if c.score >= prev + 0.8 or (prev == 0.0 and c.score >= 4.0):
                self._last_alerted[key] = c.score
                alerted = True
            lines.append(
                f"{c.symbol} | score {c.score:.2f} | last {c.last:.2f} | VWAP {c.vwap:.2f} | ATR% {c.atr_pct:.2f} | {c.notes}"
            )
        if alerted and lines:
            self.discord.send(f"{universe} Scanner ({sess})", lines)

    def _maybe_alert_gappers(self, sess: str, gappers: List[Gapper]):
        lines = []
        alerted = False
        for g in gappers[:10]:
            key = f"GAP:{g.symbol}"
            prev = self._last_alerted.get(key, 0.0)
            if g.score >= prev + 1.0 or (prev == 0.0 and abs(g.gap_pct) >= max(6.0, self.cfg.pm_gap_min_pct)):
                self._last_alerted[key] = g.score
                alerted = True
            lines.append(
                f"{g.symbol} | gap {g.gap_pct:.2f}% | last {g.last:.2f} | prev {g.prev_close:.2f} | vol {g.session_vol} | {g.notes}"
            )
        if alerted and lines:
            self.discord.send(f"Gappers ({sess})", lines)

    def _compute_gappers_from_snapshots(self, symbols: List[str], universe: str, sess: str) -> List[Gapper]:
        snaps = self.client.get_snapshots(symbols)
        out: List[Gapper] = []

        for sym in symbols:
            s = snaps.get(sym) or {}
            pb = s.get("prevDailyBar") or {}
            db = s.get("dailyBar") or {}

            try:
                prev_close = float(pb.get("c"))
            except Exception:
                continue
            if prev_close <= 0:
                continue

            last = None
            for cand in [
                (s.get("latestTrade") or {}).get("p"),
                (s.get("minuteBar") or {}).get("c"),
                db.get("c"),
                pb.get("c"),
            ]:
                try:
                    if cand is None:
                        continue
                    fx = float(cand)
                    if fx > 0:
                        last = fx
                        break
                except Exception:
                    continue
            if last is None:
                continue

            gap_pct = (last - prev_close) / prev_close * 100.0

            # In PRE/POST, dailyBar volume can be stale/zero depending on feed.
            # Prefer minuteBar.v when available; fall back to dailyBar.v.
            session_vol = 0
            try:
                mb = s.get("minuteBar") or {}
                if mb.get("v") is not None:
                    session_vol = int(float(mb.get("v") or 0))
                else:
                    session_vol = int(float(db.get("v") or 0))
            except Exception:
                session_vol = 0

            if abs(gap_pct) < self.cfg.pm_gap_min_pct:
                continue
            if session_vol < self.cfg.pm_min_volume:
                continue

            score = abs(gap_pct) + (math.log1p(session_vol) / 3.0)
            notes = "GapUp" if gap_pct > 0 else "GapDown"

            out.append(Gapper(
                symbol=sym,
                last=float(last),
                prev_close=float(prev_close),
                gap_pct=float(gap_pct),
                session_vol=int(session_vol),
                score=float(score),
                session=sess,
                universe=universe,
                notes=notes,
            ))

        out.sort(key=lambda x: x.score, reverse=True)
        return out

    def _prefilter_with_snapshots(self, symbols: List[str], universe: str) -> List[str]:
        if not symbols:
            return []

        snaps = self.client.get_snapshots(symbols)
        scored: List[Tuple[float, str]] = []

        for sym in symbols:
            s = snaps.get(sym) or {}
            db = s.get("dailyBar") or {}
            pb = s.get("prevDailyBar") or {}

            px = None
            for cand in [
                (s.get("latestTrade") or {}).get("p"),
                (s.get("minuteBar") or {}).get("c"),
                db.get("c"),
                pb.get("c"),
            ]:
                try:
                    if cand is None:
                        continue
                    fx = float(cand)
                    if fx > 0:
                        px = fx
                        break
                except Exception:
                    continue
            if px is None:
                continue

            vol = 0
            for cand in [db.get("v"), pb.get("v")]:
                try:
                    if cand is None:
                        continue
                    vol = int(float(cand))
                    break
                except Exception:
                    continue

            prev_close = None
            try:
                pc = pb.get("c")
                if pc is not None:
                    prev_close = float(pc)
            except Exception:
                prev_close = None

            move = 0.0
            if prev_close and prev_close > 0:
                move = abs((px - prev_close) / prev_close)

            score = math.log1p(vol) + (move * 5.0)

            if universe == "NORMAL":
                if not (self.cfg.normal_min_price <= px <= self.cfg.normal_max_price):
                    continue
            else:
                if not (self.cfg.penny_min_price <= px <= self.cfg.penny_max_price):
                    continue

            scored.append((score, sym))

        scored.sort(reverse=True, key=lambda x: x[0])
        return [sym for _, sym in scored[: self.cfg.prefilter_top_k]]

    def _is_rate_limit_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        if "429" in msg:
            return True
        if "rate limit" in msg or "too many requests" in msg:
            return True
        return False

    def _increase_backoff(self) -> None:
        if self._backoff_s <= 0:
            self._backoff_s = self._backoff_step_s
        else:
            self._backoff_s = min(self._backoff_max_s, self._backoff_s + max(self._backoff_step_s, self._backoff_s * 0.5))

    # ---------------- Patch 6: Snapshot movers ----------------

    def _snapshot_movers(self, symbols: List[str], universe: str, sess: str) -> List[Candidate]:
        if not symbols:
            return []

        snaps = self.client.get_snapshots(symbols)
        out: List[Candidate] = []

        if universe == "NORMAL":
            min_move = float(self.cfg.snap_min_move_pct_normal)
            min_vol = int(self.cfg.snap_min_volume_normal)
            min_price = float(self.cfg.normal_min_price)
            max_price = float(self.cfg.normal_max_price)
        else:
            min_move = float(self.cfg.snap_min_move_pct_penny)
            min_vol = int(self.cfg.snap_min_volume_penny)
            min_price = float(self.cfg.penny_min_price)
            max_price = float(self.cfg.penny_max_price)

        for sym in symbols:
            s = snaps.get(sym) or {}
            pb = s.get("prevDailyBar") or {}
            db = s.get("dailyBar") or {}

            try:
                prev_close = float(pb.get("c"))
            except Exception:
                continue
            if prev_close <= 0:
                continue

            last = None
            for cand in [
                (s.get("latestTrade") or {}).get("p"),
                (s.get("minuteBar") or {}).get("c"),
                db.get("c"),
                pb.get("c"),
            ]:
                try:
                    if cand is None:
                        continue
                    fx = float(cand)
                    if fx > 0:
                        last = fx
                        break
                except Exception:
                    continue
            if last is None:
                continue

            if not (min_price <= last <= max_price):
                continue

            try:
                vol = int(float(db.get("v") or 0))
            except Exception:
                vol = 0
            if vol < min_vol:
                continue

            move_pct = (last - prev_close) / prev_close * 100.0
            if abs(move_pct) < min_move:
                continue

            score = abs(move_pct) + (math.log1p(vol) / 3.0)
            notes = f"SnapMove {move_pct:+.2f}% | vol {vol}"

            out.append(Candidate(
                symbol=sym,
                last=float(last),
                vwap=0.0,
                ema9=0.0,
                ema20=0.0,
                ema50=0.0,
                atr_pct=float(abs(move_pct)),   # proxy
                vol_spike=float(math.log1p(vol)),
                score=float(score),
                session=sess,
                universe=universe,
                notes=notes,
            ))

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:25]

    # ---------------- Patch 8: persistence + A+ tagging ----------------

    def _update_persistence(self, symbols_seen: List[str]):
        current = set(symbols_seen)

        for sym in current:
            self._persistence[sym] = self._persistence.get(sym, 0) + 1

        for sym in list(self._persistence.keys()):
            if sym not in current:
                self._persistence[sym] = max(0, self._persistence[sym] - 1)
                if self._persistence[sym] == 0:
                    del self._persistence[sym]

    def _annotate_candidates(self, cands: List[Candidate]) -> None:
        for c in cands:
            p = self._persistence.get(c.symbol, 0)
            is_aplus = self._is_aplus(c, p)
            suffix = f"A+ ({p}x)" if is_aplus else f"P{p}"
            if c.notes:
                if "A+ (" in c.notes or " | P" in c.notes or c.notes.startswith("P"):
                    continue
                c.notes = f"{c.notes} | {suffix}"
            else:
                c.notes = suffix

    def _is_aplus(self, c: Candidate, persistence: int) -> bool:
        # Snapshot mover rows: VWAP=0 -> never qualify as A+ momentum rows
        if c.vwap <= 0:
            return False
        if persistence < self.cfg.aplus_min_persistence:
            return False
        if c.last <= c.vwap:
            return False
        if c.vol_spike < self.cfg.aplus_min_vol_spike:
            return False
        if not (self.cfg.aplus_min_atr_pct <= c.atr_pct <= self.cfg.aplus_max_atr_pct):
            return False
        if not (c.ema9 > c.ema20 > c.ema50):
            return False
        return True


    # ---------------- Patch 11: news attach ----------------

    def _attach_news_to_rows(self, normal: List[Candidate], penny: List[Candidate], gappers: List[Gapper]) -> None:
        """
        Attaches latest headline (if any) to candidates/gappers.
        Fails soft if Alpaca News is not available for the account.
        """
        syms: List[str] = []
        for c in (normal + penny):
            if c.symbol:
                syms.append(c.symbol)
        for g in gappers:
            if g.symbol:
                syms.append(g.symbol)
        # unique, preserve order
        seen = set()
        uniq = []
        for s in syms:
            s = s.strip().upper()
            if not s or s in seen:
                continue
            seen.add(s)
            uniq.append(s)

        hits = self.news.get_latest_for_symbols(uniq)
        for c in (normal + penny):
            hit = hits.get(c.symbol)
            if not hit:
                continue
            c.news_headline = hit.headline
            c.news_source = hit.source
            c.news_url = hit.url
            c.news_age_min = self.news._age_minutes(hit.created_at)
            c.catalyst = getattr(hit, 'catalyst', '') or ''
            c.risk_flag = getattr(hit, 'risk_flag', '') or ''

        for g in gappers:
            hit = hits.get(g.symbol)
            if not hit:
                continue
            g.news_headline = hit.headline
            g.news_source = hit.source
            g.news_url = hit.url
            g.news_age_min = self.news._age_minutes(hit.created_at)
            g.catalyst = getattr(hit, 'catalyst', '') or ''
            g.risk_flag = getattr(hit, 'risk_flag', '') or ''
    # ---------------- Patch 10: gappers persistence + A+ gappers ----------------


    def _tag_readiness(self, normal: List[Candidate], penny: List[Candidate], gappers: List[Gapper]) -> None:
        """Deterministic 'trade-readiness' tagging.
        This does NOT place trades. It simply marks rows as READY when they pass a strict rule set.
        READY rows should be the first ones you chart / consider.
        """
        # Candidates (REG-style momentum readiness)
        for c in list(normal) + list(penny):
            try:
                p = self._persistence.get(c.symbol, 0)
                is_penny = (c.universe.upper() == "PENNY")
                min_score = self.cfg.penny_min_score if is_penny else self.cfg.normal_min_score

                # Baseline: must have bars-derived structure (VWAP/EMAs)
                if c.vwap <= 0 or c.ema9 <= 0 or c.ema20 <= 0:
                    c.ready = 0
                    c.ready_reason = "NoBars"
                    continue

                # Risk veto
                rf = (c.risk_flag or "").upper()
                if "DILUTION" in rf or "REVERSE" in rf:
                    c.ready = 0
                    c.ready_reason = "RiskVeto"
                    continue

                # Trend + control
                if not (c.last > c.vwap and c.ema9 > c.ema20 > c.ema50):
                    c.ready = 0
                    c.ready_reason = "NoTrend"
                    continue

                # Volatility + participation
                if not (self.cfg.aplus_min_atr_pct <= c.atr_pct <= self.cfg.aplus_max_atr_pct):
                    c.ready = 0
                    c.ready_reason = "ATRRange"
                    continue
                if c.vol_spike < self.cfg.aplus_min_vol_spike:
                    c.ready = 0
                    c.ready_reason = "NoVolSpike"
                    continue
                if p < self.cfg.aplus_min_persistence:
                    c.ready = 0
                    c.ready_reason = "NoPersist"
                    continue
                if c.score < min_score:
                    c.ready = 0
                    c.ready_reason = "LowScore"
                    continue

                # If we have a strong catalyst, reflect it; otherwise mark as TECH
                cat = (c.catalyst or "").upper()
                if cat and cat != "UNKNOWN":
                    c.ready = 1
                    c.ready_reason = cat
                else:
                    c.ready = 1
                    c.ready_reason = "TECH"
            except Exception:
                c.ready = 0
                c.ready_reason = "Err"

        # Gappers (PRE/POST readiness)
        for g in gappers:
            try:
                gp = self._gapper_persistence.get(g.symbol, 0)
                rf = (g.risk_flag or "").upper()
                if "DILUTION" in rf or "REVERSE" in rf:
                    g.ready = 0
                    g.ready_reason = "RiskVeto"
                    continue

                # Use GAPPLUS thresholds (strict)
                if gp < self.cfg.gapplus_min_persistence:
                    g.ready = 0
                    g.ready_reason = "NoPersist"
                    continue
                if abs(g.gap_pct) < self.cfg.gapplus_min_gap_pct:
                    g.ready = 0
                    g.ready_reason = "GapSmall"
                    continue
                if g.session_vol < self.cfg.gapplus_min_volume:
                    g.ready = 0
                    g.ready_reason = "LowVol"
                    continue
                if not (self.cfg.gapplus_min_price <= g.last <= self.cfg.gapplus_max_price):
                    g.ready = 0
                    g.ready_reason = "PriceRange"
                    continue

                cat = (g.catalyst or "").upper()
                if cat and cat != "UNKNOWN":
                    g.ready = 1
                    g.ready_reason = cat
                else:
                    g.ready = 1
                    g.ready_reason = "GAP+VOL"
            except Exception:
                g.ready = 0
                g.ready_reason = "Err"
    def _update_gapper_persistence(self, symbols_seen: List[str]):
        current = set(symbols_seen)

        for sym in current:
            self._gapper_persistence[sym] = self._gapper_persistence.get(sym, 0) + 1

        for sym in list(self._gapper_persistence.keys()):
            if sym not in current:
                self._gapper_persistence[sym] = max(0, self._gapper_persistence[sym] - 1)
                if self._gapper_persistence[sym] == 0:
                    del self._gapper_persistence[sym]

    def _is_gapplus(self, g: Gapper, persistence: int) -> bool:
        if persistence < self.cfg.gapplus_min_persistence:
            return False
        if abs(g.gap_pct) < self.cfg.gapplus_min_gap_pct:
            return False
        if g.session_vol < self.cfg.gapplus_min_volume:
            return False
        if not (self.cfg.gapplus_min_price <= g.last <= self.cfg.gapplus_max_price):
            return False
        return True

    def _annotate_gappers(self, gappers: List[Gapper]) -> None:
        for g in gappers:
            p = self._gapper_persistence.get(g.symbol, 0)
            is_gp = self._is_gapplus(g, p)
            suffix = f"A+G ({p}x)" if is_gp else f"P{p}"
            if g.notes:
                if "A+G (" in g.notes or " | P" in g.notes:
                    continue
                g.notes = f"{g.notes} | {suffix}"
            else:
                g.notes = suffix

    # ---------------- Patch 9: export ----------------

    def _has_exportworthy(self, normal: List[Candidate], penny: List[Candidate], gappers: List[Gapper]) -> bool:
        for c in (normal + penny):
            if "A+ (" in c.notes:
                return True
        for g in gappers:
            if "A+G (" in g.notes:
                return True
        return bool(gappers)

    def export_watchlist(self) -> str:
        ts = now_et()
        stamp = ts.strftime("%Y%m%d_%H%M%S_ET")
        outdir = Path(self.cfg.export_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        normal = self._last_normal[:]
        penny = self._last_penny[:]
        gappers = self._last_gappers[:]

        def _rank_key(c: Candidate):
            is_ap = ("A+ (" in c.notes)
            return (0 if is_ap else 1, -c.score, c.symbol)

        normal_sorted = sorted(normal, key=_rank_key)[: self.cfg.export_top_n]
        penny_sorted = sorted(penny, key=_rank_key)[: self.cfg.export_top_n]
        gappers_sorted = sorted(
            gappers,
            key=lambda g: (0 if ("A+G (" in g.notes) else 1, -abs(g.gap_pct), -g.session_vol, g.symbol),
        )[: self.cfg.export_top_n]

        payload = {
            "asof_et": ts.isoformat(),
            "session": session_name(ts),
            "mode": self._session_mode,
            "normal": [c.__dict__ for c in normal_sorted],
            "penny": [c.__dict__ for c in penny_sorted],
            "gappers": [g.__dict__ for g in gappers_sorted],
        }

        base = outdir / f"watchlist_{stamp}"

        if self.cfg.export_write_json == 1:
            with open(str(base) + ".json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)

        if self.cfg.export_write_csv == 1:
            rows = []
            for u, arr in (("NORMAL", normal_sorted), ("PENNY", penny_sorted)):
                for c in arr:
                    rows.append({
                        "universe": u,
                        "symbol": c.symbol,
                        "score": f"{c.score:.2f}",
                        "last": f"{c.last:.4f}",
                        "vwap": f"{c.vwap:.4f}",
                        "ema9": f"{c.ema9:.4f}",
                        "ema20": f"{c.ema20:.4f}",
                        "ema50": f"{c.ema50:.4f}",
                        "atr_pct": f"{c.atr_pct:.2f}",
                        "vol_spike": f"{c.vol_spike:.2f}",
                        "news": (c.news_headline or ""),
                        "news_source": (c.news_source or ""),
                        "news_age_min": str(c.news_age_min or 0),
                        "news_url": (c.news_url or ""),
                        "catalyst": (c.catalyst or ""),
                        "risk_flag": (c.risk_flag or ""),
                        "notes": c.notes,
                        "session": c.session,
                    })

            csv_path = str(base) + ".csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                if rows:
                    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
                else:
                    f.write("universe,symbol\n")

        # Gate 1: emit candidates to universe inbox (optional)
        if getattr(self, "universe_adapter", None):
            try:
                combined = normal_sorted + penny_sorted
                combined = sorted(combined, key=lambda c: (-c.score, c.symbol))[: max(1, int(self.cfg.export_top_n))]
                for c in combined:
                    sess = str(c.session).upper()
                    session = "rth" if sess in ("REG", "RTH", "OPEN") else "pre"
                    features = {
                        "price": float(c.last),
                        "last": float(c.last),
                        "vwap": float(c.vwap),
                        "ema9": float(c.ema9),
                        "ema20": float(c.ema20),
                        "ema50": float(c.ema50),
                        "atr_pct": float(c.atr_pct),
                        "vol_spike": float(c.vol_spike),
                        "universe": str(c.universe),
                        "notes": str(c.notes),
                    }
                    # Levels are optional; Gate 2 should not rely on them being present
                    self.universe_adapter.write_candidate(
                        symbol=c.symbol,
                        score=float(c.score),
                        session=session,
                        features=features,
                        levels={},
                        source="scanner_v2",
                        version="2.1",
                    )
            except Exception:
                # Never break export flow
                pass

        return str(base)

    def _session_relax_factor(self, sess: str) -> float:
        """
        Returns a multiplicative relax factor for thresholds based on session.
        PRE/POST are thinner/noisier, so we typically relax thresholds slightly.
        """
        s = (sess or "").upper()
        if s == "PRE":
            return float(self.session_pre_relax)
        if s == "POST":
            return float(self.session_post_relax)
        return float(self.session_reg_relax)


# ----------------------------
# GUI (Dark Mode)
# ----------------------------

def apply_dark_mode(root: tk.Tk) -> None:
    DARK_BG = "#0F0F10"
    PANEL_BG = "#151517"
    WIDGET_BG = "#1C1C1F"
    TEXT = "#EAEAEA"
    MUTED = "#A8A8A8"
    BORDER = "#2A2A2D"
    SELECT_BG = "#2D2D33"
    SELECT_TEXT = "#FFFFFF"

    root.configure(bg=DARK_BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=DARK_BG, foreground=TEXT)
    style.configure("TFrame", background=DARK_BG)
    style.configure("TLabel", background=DARK_BG, foreground=TEXT)

    style.configure("TButton",
                    background=WIDGET_BG,
                    foreground=TEXT,
                    bordercolor=BORDER,
                    focusthickness=1,
                    focuscolor=BORDER,
                    padding=(10, 6))
    style.map("TButton",
              background=[("active", "#232327"), ("disabled", PANEL_BG)],
              foreground=[("disabled", MUTED)],
              bordercolor=[("active", BORDER), ("disabled", BORDER)])

    style.configure("TNotebook", background=DARK_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL_BG, foreground=TEXT, padding=(12, 6), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", DARK_BG), ("active", "#232327")],
              foreground=[("selected", SELECT_TEXT), ("active", SELECT_TEXT)])

    style.configure("Treeview",
                    background=DARK_BG,
                    fieldbackground=DARK_BG,
                    foreground=TEXT,
                    rowheight=44,
                    bordercolor=BORDER,
                    lightcolor=BORDER,
                    darkcolor=BORDER)
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", SELECT_TEXT)])

    style.configure("Treeview.Heading", background=PANEL_BG, foreground=SELECT_TEXT, relief="flat")
    style.map("Treeview.Heading",
              background=[("active", "#232327")],
              foreground=[("active", SELECT_TEXT)])

    style.configure("TEntry", fieldbackground=WIDGET_BG, foreground=TEXT, background=WIDGET_BG)
    style.configure("TCombobox", fieldbackground=WIDGET_BG, foreground=TEXT)



class TreeviewHoverTooltip:
    """Simple hover tooltip for ttk.Treeview cells (works on Windows 11).

    ttk.Treeview does not support true text wrapping or full-cell expansion.
    This tooltip shows the full cell contents (especially NEWS) on hover.
    """

    def __init__(self, root: tk.Tk, delay_ms: int = 450):
        self.root = root
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        self._label: Optional[tk.Label] = None
        self._last = (None, None)  # (iid, col_index)

    def attach(self, tree: ttk.Treeview) -> None:
        tree.bind("<Motion>", lambda e, tr=tree: self._on_motion(tr, e), add="+")
        tree.bind("<Leave>", lambda e: self.hide(), add="+")
        tree.bind("<ButtonPress>", lambda e: self.hide(), add="+")
        tree.bind("<MouseWheel>", lambda e: self.hide(), add="+")  # windows scroll

    def hide(self) -> None:
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._last = (None, None)
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
            self._label = None

    def _on_motion(self, tree: ttk.Treeview, event: tk.Event) -> None:
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            self.hide()
            return

        iid = tree.identify_row(event.y)
        col = tree.identify_column(event.x)  # '#1', '#2', ...
        if not iid or not col or not col.startswith("#"):
            self.hide()
            return

        try:
            col_index = int(col[1:]) - 1
        except Exception:
            self.hide()
            return

        if (iid, col_index) == self._last:
            return  # same cell, do nothing

        self._last = (iid, col_index)

        # schedule tooltip show (debounced)
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        # capture current mouse position in screen coords
        x_root = event.x_root
        y_root = event.y_root

        def _fire():
            self._after_id = None
            txt = self._get_cell_text(tree, iid, col_index)
            if not txt:
                self.hide()
                return
            # Only show for longer values or multiline (NEWS / NOTES)
            if len(txt) < 24 and "\n" not in txt:
                self.hide()
                return
            self._show(txt, x_root + 12, y_root + 16)

        self._after_id = self.root.after(self.delay_ms, _fire)

    def _get_cell_text(self, tree: ttk.Treeview, iid: str, col_index: int) -> str:
        try:
            values = tree.item(iid, "values") or ()
            if col_index < 0 or col_index >= len(values):
                return ""
            val = values[col_index]
            if val is None:
                return ""
            return str(val)
        except Exception:
            return ""

    def _show(self, text: str, x: int, y: int) -> None:
        # recreate every time (cheap + avoids stale state)
        self.hide()
        tip = tk.Toplevel(self.root)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        tip.configure(bg="#0f0f10")

        lbl = tk.Label(
            tip,
            text=text,
            justify="left",
            anchor="w",
            bg="#0f0f10",
            fg="#e6e6e6",
            padx=10,
            pady=8,
            wraplength=540,
            font=("Segoe UI", 9),
        )
        lbl.pack()

        # clamp to screen
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            tip.update_idletasks()
            w = tip.winfo_width()
            h = tip.winfo_height()
            x2 = min(max(0, x), max(0, sw - w - 8))
            y2 = min(max(0, y), max(0, sh - h - 8))
            tip.geometry(f"+{x2}+{y2}")
        except Exception:
            tip.geometry(f"+{x}+{y}")

        self._tip = tip
        self._label = lbl


class ScannerGUI:
    def __init__(self, root: tk.Tk, cfg: ScannerConfig):
        self.root = root
        apply_dark_mode(self.root)

        self.cfg = cfg
        self.root.title("Standalone Market Scanner (GUI + Discord)")

        self._sort_state: Dict[Tuple[int, str], bool] = {}  # (id(tree), col) -> reverse
        self.root.geometry("1300x720")

        self.status_var = tk.StringVar(value="Status: idle")
        self.error_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value="AUTO")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")

        self.btn_start = ttk.Button(top, text="Start", command=self.on_start)
        self.btn_stop = ttk.Button(top, text="Stop", command=self.on_stop, state="disabled")
        self.btn_scan_once = ttk.Button(top, text="Scan Once", command=self.on_scan_once)
        self.btn_export = ttk.Button(top, text="Export Watchlist", command=self.on_export)
        self.btn_clear = ttk.Button(top, text="Clear State", command=self.on_clear_state)

        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop.pack(side="left", padx=(0, 6))
        self.btn_scan_once.pack(side="left", padx=(0, 6))
        self.btn_export.pack(side="left", padx=(0, 6))
        self.btn_clear.pack(side="left", padx=(0, 18))

        ttk.Label(top, text="Mode:").pack(side="left", padx=(0, 6))
        self.mode_combo = ttk.Combobox(top, textvariable=self.mode_var, state="readonly",
                                       values=("AUTO", "REG", "PREPOST", "CLOSED"), width=10)
        self.mode_combo.pack(side="left", padx=(0, 18))
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_mode_change)

        ttk.Label(top, textvariable=self.status_var).pack(side="left")
        self.error_lbl = tk.Label(top, textvariable=self.error_var, fg="#FF5C5C", bg="#0F0F10")
        self.error_lbl.pack(side="right")

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True)

        self.normal_tab = ttk.Frame(self.nb, padding=6)
        self.penny_tab = ttk.Frame(self.nb, padding=6)
        self.gappers_tab = ttk.Frame(self.nb, padding=6)

        self.nb.add(self.normal_tab, text="Normal")
        self.nb.add(self.penny_tab, text="Penny (scan-only)")
        self.nb.add(self.gappers_tab, text="Gappers (PRE/POST)")

        self.normal_tree = self._make_candidate_tree(self.normal_tab)
        self.penny_tree = self._make_candidate_tree(self.penny_tab)
        self.gappers_tree = self._make_gappers_tree(self.gappers_tab)

        
        # Row metadata for UI actions (news/chart/open/copy)
        # key: (id(tree), iid) -> dict(symbol=..., news_url=..., headline=...)
        self._row_meta: Dict[Tuple[int, str], Dict[str, str]] = {}
        self._ctx_tree: Optional[ttk.Treeview] = None
        self._ctx_iid: Optional[str] = None
        self._make_context_menu()
# Hover tooltips (shows full NEWS / long cells)
        self._tooltip = TreeviewHoverTooltip(self.root)
        self._tooltip.attach(self.normal_tree)
        self._tooltip.attach(self.penny_tree)
        self._tooltip.attach(self.gappers_tree)


        # Row actions (Patch 16): double-click open news, right-click context menu
        self._bind_tree_actions(self.normal_tree)
        self._bind_tree_actions(self.penny_tree)
        self._bind_tree_actions(self.gappers_tree)
        # Row highlighting (Patch 8 + 10)
        self.normal_tree.tag_configure("aplus", background="#1f3d2b")
        self.normal_tree.tag_configure("above_vwap", background="#1e2a3a")
        self.normal_tree.tag_configure("snapshot", background="#2a2a2d")

        self.penny_tree.tag_configure("aplus", background="#3d1f2b")
        self.penny_tree.tag_configure("above_vwap", background="#2a1e3a")
        self.penny_tree.tag_configure("snapshot", background="#2a2a2d")

        self.gappers_tree.tag_configure("gapper", background="#2a213a")
        self.gappers_tree.tag_configure("gapper_aplus", background="#1f3d2b")

        self.engine = ScannerEngine(cfg, self.ui_update)
        self.engine.set_session_mode(self.mode_var.get())
        self._install_tk_exception_hook()

    
    def _wrap_cell(self, s: str, width: int = 60, max_lines: int = 2) -> str:
        """Best-effort text wrapping for Treeview cells (Treeview doesn't truly wrap).
        We insert newlines and increase rowheight so you can read NEWS without a tooltip.
        """
        if not s:
            return ""
        s = str(s).strip()
        if width <= 5:
            return s
        words = s.split(" ")
        lines: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for w in words:
            if not w:
                continue
            add_len = len(w) + (1 if cur else 0)
            if cur_len + add_len > width and cur:
                lines.append(" ".join(cur))
                cur = [w]
                cur_len = len(w)
                if len(lines) >= max_lines:
                    break
            else:
                cur.append(w)
                cur_len += add_len
        if len(lines) < max_lines and cur:
            lines.append(" ".join(cur))
        out = "\n".join(lines[:max_lines])
        # if we truncated, add ellipsis
        if len(lines) >= max_lines and len(words) > 0:
            if not out.endswith("…") and len(out) >= 1:
                out = out.rstrip()
                out = out[:-1] + "…" if len(out) > 1 else out + "…"
        return out

    def _parse_sort_value(self, v: str):
        """Parse displayed cell value into sortable python value."""
        if v is None:
            return ""
        s = str(v).strip()
        if s == "":
            return ""
        # percents
        if s.endswith("%"):
            try:
                return float(s[:-1].replace(",", ""))
            except Exception:
                return s
        # plain numbers
        try:
            return float(s.replace(",", ""))
        except Exception:
            return s.lower()

    def _sort_treeview(self, tree: ttk.Treeview, col: str) -> None:
        """Click-to-sort for a Treeview heading."""
        key = (id(tree), col)
        reverse = self._sort_state.get(key, False)

        items = list(tree.get_children(""))
        # build sortable list
        decorated = []
        for iid in items:
            val = tree.set(iid, col)
            decorated.append((self._parse_sort_value(val), iid))

        decorated.sort(key=lambda x: x[0], reverse=reverse)

        for idx, (_, iid) in enumerate(decorated):
            tree.move(iid, "", idx)

        # toggle
        self._sort_state[key] = not reverse
    def _make_candidate_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        cols = ("symbol", "score", "ready", "last", "vwap", "ema9", "ema20", "ema50", "atr_pct", "vol_spike", "news", "notes")
        tree = ttk.Treeview(parent, columns=cols, show="headings", height=24)
        for c in cols:
            tree.heading(c, text=c.upper(), command=lambda col=c, tr=tree: self._sort_treeview(tr, col))
            if c == "symbol":
                w = 90
            elif c == "ready":
                w = 130
            elif c in ("score", "last", "vwap", "ema9", "ema20", "ema50", "atr_pct", "vol_spike"):
                w = 95
            elif c == "news":
                w = 520
            elif c == "notes":
                w = 260
            else:
                w = 120
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True)
        return tree

    def _make_gappers_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        cols = ("symbol", "gap_pct", "ready", "last", "prev_close", "session_vol", "score", "universe", "news", "notes")
        tree = ttk.Treeview(parent, columns=cols, show="headings", height=24)
        for c in cols:
            tree.heading(c, text=c.upper(), command=lambda col=c, tr=tree: self._sort_treeview(tr, col))
            if c == "symbol":
                w = 90
            elif c == "ready":
                w = 130
            elif c == "ready":
                w = 130
            elif c == "universe":
                w = 110
            elif c in ("gap_pct", "last", "prev_close", "session_vol", "score"):
                w = 120
            elif c == "news":
                w = 520
            elif c == "notes":
                w = 260
            else:
                w = 140
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True)
        return tree

    def on_mode_change(self, _evt=None):
        self.engine.set_session_mode(self.mode_var.get())
        # immediate UI feedback
        self.status_var.set(f"Status: mode set -> {self.mode_var.get()}")

    def on_clear_state(self):
        self.engine.clear_state()
        self.status_var.set("Status: state cleared")
        self.error_var.set("")

    def on_start(self):
        self.engine.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set("Status: running...")

    def on_stop(self):
        self.engine.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Status: stopped")

    def on_scan_once(self):
        """
        Run scan_once in a worker thread so:
          - UI doesn't freeze
          - exceptions don't kill the app
        """
        self.btn_scan_once.config(state="disabled")
        self.error_var.set("")

        def _worker():
            try:
                self.engine.scan_once()
            except BaseException as e:
                tb = traceback.format_exc()
                # marshal back to UI thread
                self.root.after(0, lambda: self._show_error(f"ScanOnce failed: {e}\n{tb}"))
            finally:
                self.root.after(0, lambda: self.btn_scan_once.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()


    def on_export(self):
        try:
            base = self.engine.export_watchlist()
            self.status_var.set(f"Status: exported -> {base}")
        except Exception as e:
            self.error_var.set(str(e))

    def ui_update(self, **kwargs):
        # Never let UI updates kill the Tk mainloop.
        def _safe():
            try:
                self._ui_update_main(**kwargs)
            except Exception as e:
                try:
                    import traceback as _tb
                    self.error_var.set("UI error: " + str(e))
                    # also print full traceback to console for debugging
                    print("[ScannerGUI] UI update error:", e)
                    print(_tb.format_exc())
                except Exception:
                    pass
        self.root.after(0, _safe)
    def _show_error(self, msg: str) -> None:
        # keep it readable in the UI
        if not msg:
            self.error_var.set("")
            return
        # avoid nuking your GUI layout with huge traces
        max_len = 1500
        if len(msg) > max_len:
            msg = msg[:max_len] + "…"
        self.error_var.set(msg)

    def _install_tk_exception_hook(self) -> None:
        """
        Capture exceptions thrown inside Tk callbacks (root.after, button handlers, etc.)
        so they don't appear as 'silent crashes'.
        """
        def _tk_report_callback_exception(exc, val, tb):
            tb_str = "".join(traceback.format_exception(exc, val, tb))
            self._show_error(f"Tk callback exception: {val}\n{tb_str}")

        # override Tk's default exception handler
        self.root.report_callback_exception = _tk_report_callback_exception

    def _ui_update_main(self, timestamp=None, session=None, mode=None, normal=None, penny=None, gappers=None, stats=None, error=None, **_):
        if error:
            self.error_var.set(f"Error: {error}")
        else:
            self.error_var.set("")

        if timestamp and session:
            if stats:
                self.status_var.set(
                    f"Status: {session} | Mode {stats.mode} | Last: {timestamp} | "
                    f"Batches N/P {stats.normal_batch}/{stats.penny_batch} | "
                    f"Picks N/P {stats.normal_pick}/{stats.penny_pick} | "
                    f"Hits N/P/G {stats.normal_hits}/{stats.penny_hits}/{stats.gappers_hits} | "
                    f"Backoff {stats.backoff_s:.0f}s"
                )
            else:
                self.status_var.set(f"Status: {session} | Mode {mode or ''} | Last scan: {timestamp}")

        if normal is not None:
            self._fill_candidate_tree(self.normal_tree, normal)
        if penny is not None:
            self._fill_candidate_tree(self.penny_tree, penny)
        if gappers is not None:
            self._fill_gappers_tree(self.gappers_tree, gappers)


    def _format_news_cell(self, obj) -> str:
        """
        Formats NEWS column. Includes catalyst + risk flag if present.
        """
        headline = getattr(obj, "news_headline", "") or ""
        if not headline:
            return ""
        source = getattr(obj, "news_source", "") or ""
        age_min = getattr(obj, "news_age_min", 0) or 0
        catalyst = getattr(obj, "catalyst", "") or ""
        risk = getattr(obj, "risk_flag", "") or ""

        try:
            age_s = f"{int(age_min)}m"
        except Exception:
            age_s = ""

        tag_parts = []
        if catalyst:
            tag_parts.append(catalyst)
        if risk:
            # visually loud
            tag_parts.append(f"⚠{risk}")
        tag = "/".join(tag_parts)

        # headline already truncated by news clients, but keep safe
        head_s = str(headline).replace("\n", " ").strip()
        max_chars = getattr(self, "news_max_chars", 90)
        try:
            max_chars = int(max_chars)
        except Exception:
            max_chars = 90
        if len(head_s) > max_chars:
            head_s = head_s[: max_chars - 1].rstrip() + "…"

        bits = []
        if age_s:
            bits.append(age_s)
        if tag:
            bits.append(tag)
        if source:
            bits.append(source + ":")
        bits.append(head_s)
        return self._wrap_cell(" ".join([b for b in bits if b]).strip(), width=72, max_lines=2)


    # ----------------------------
    # Patch 16: Row actions (open/copy/watchlist)
    # ----------------------------

    def _make_context_menu(self) -> None:
        self._ctx_menu = tk.Menu(self.root, tearoff=0, bg="#0f1216", fg="#e6e6e6",
                                 activebackground="#1e2a3a", activeforeground="#ffffff")
        self._ctx_menu.add_command(label="Open News", command=self._ctx_open_news)
        self._ctx_menu.add_command(label="Open Chart", command=self._ctx_open_chart)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Copy Symbol", command=self._ctx_copy_symbol)
        self._ctx_menu.add_command(label="Copy Headline", command=self._ctx_copy_headline)
        self._ctx_menu.add_command(label="Copy URL", command=self._ctx_copy_url)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Send to Watchlist", command=self._ctx_send_watchlist)

    def _bind_tree_actions(self, tree: ttk.Treeview) -> None:
        tree.bind("<Double-1>", lambda e, tr=tree: self._on_tree_double_click(e, tr))
        # Windows right-click
        tree.bind("<Button-3>", lambda e, tr=tree: self._on_tree_right_click(e, tr))
        # Track selection changes too (helps context menu)
        tree.bind("<<TreeviewSelect>>", lambda e, tr=tree: self._on_tree_select(e, tr))

    def _on_tree_select(self, _evt, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if sel:
            self._ctx_tree = tree
            self._ctx_iid = sel[0]

    def _on_tree_double_click(self, event, tree: ttk.Treeview) -> None:
        iid = tree.identify_row(event.y)
        if not iid:
            sel = tree.selection()
            iid = sel[0] if sel else None
        if not iid:
            return
        self._ctx_tree = tree
        self._ctx_iid = iid
        # default action: open news (fallback to chart if none)
        if not self._open_news_for_current():
            self._open_chart_for_current()

    def _on_tree_right_click(self, event, tree: ttk.Treeview) -> None:
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
            self._ctx_tree = tree
            self._ctx_iid = iid
        else:
            sel = tree.selection()
            self._ctx_tree = tree
            self._ctx_iid = sel[0] if sel else None
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._ctx_menu.grab_release()
            except Exception:
                pass

    def _meta_for_current(self) -> Dict[str, str]:
        if not getattr(self, "_ctx_tree", None) or not getattr(self, "_ctx_iid", None):
            return {}
        return self._row_meta.get((id(self._ctx_tree), self._ctx_iid), {})

    def _open_news_for_current(self) -> bool:
        meta = self._meta_for_current()
        url = (meta.get("news_url") or "").strip()
        if not url:
            return False
        try:
            webbrowser.open(url)
            return True
        except Exception as e:
            self._show_error(f"Failed to open news URL: {e}")
            return False

    def _open_chart_for_current(self) -> bool:
        meta = self._meta_for_current()
        sym = (meta.get("symbol") or "").strip()
        if not sym:
            return False
        # TradingView symbol page (works without an API key)
        url = f"https://www.tradingview.com/symbols/{sym}/"
        try:
            webbrowser.open(url)
            return True
        except Exception as e:
            self._show_error(f"Failed to open chart URL: {e}")
            return False

    def _clipboard_set(self, s: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(s)
            self.root.update_idletasks()
        except Exception:
            pass

    def _ctx_open_news(self) -> None:
        if not self._open_news_for_current():
            self._show_error("No news URL for this row.")

    def _ctx_open_chart(self) -> None:
        if not self._open_chart_for_current():
            self._show_error("No symbol for this row.")

    def _ctx_copy_symbol(self) -> None:
        meta = self._meta_for_current()
        sym = (meta.get("symbol") or "").strip()
        if sym:
            self._clipboard_set(sym)

    def _ctx_copy_headline(self) -> None:
        meta = self._meta_for_current()
        h = (meta.get("headline") or "").strip()
        if h:
            self._clipboard_set(h)

    def _ctx_copy_url(self) -> None:
        meta = self._meta_for_current()
        u = (meta.get("news_url") or "").strip()
        if u:
            self._clipboard_set(u)

    def _ctx_send_watchlist(self) -> None:
        meta = self._meta_for_current()
        sym = (meta.get("symbol") or "").strip()
        if not sym:
            self._show_error("No symbol to add.")
            return
        try:
            out_dir = getattr(self.cfg, "export_dir", "exports")
            os.makedirs(out_dir, exist_ok=True)
            wl_path = os.path.join(out_dir, "scanner_watchlist.txt")
            existing = set()
            if os.path.exists(wl_path):
                with open(wl_path, "r", encoding="utf-8") as f:
                    existing = {ln.strip().upper() for ln in f if ln.strip()}
            if sym.upper() not in existing:
                with open(wl_path, "a", encoding="utf-8") as f:
                    f.write(sym.upper() + "\n")
            self._show_error(f"Added to watchlist: {sym.upper()}")
        except Exception as e:
            self._show_error(f"Failed to write watchlist: {e}")

    def _fill_candidate_tree(self, tree: ttk.Treeview, rows: List[Candidate]):
        tree.delete(*tree.get_children())
        # clear stored metadata for this tree
        tid = id(tree)
        self._row_meta = {k: v for k, v in self._row_meta.items() if k[0] != tid}
        cols = tuple(tree["columns"])
        for r in rows:
            tags: List[str] = []
            if r.vwap <= 0.0 and r.notes.startswith("SnapMove"):
                tags.append("snapshot")
            elif "A+ (" in r.notes:
                tags.append("aplus")
            elif r.vwap > 0.0 and r.last > r.vwap:
                tags.append("above_vwap")

            colmap = {
                "symbol": r.symbol,
                "score": f"{r.score:.2f}",
                "ready": (f"YES: {r.ready_reason}" if getattr(r, "ready", 0) else ""),
                "last": f"{r.last:.2f}",
                "vwap": f"{r.vwap:.2f}" if r.vwap else "0.00",
                "ema9": f"{r.ema9:.2f}" if r.ema9 else "0.00",
                "ema20": f"{r.ema20:.2f}" if r.ema20 else "0.00",
                "ema50": f"{r.ema50:.2f}" if r.ema50 else "0.00",
                "atr_pct": f"{r.atr_pct:.2f}",
                "vol_spike": f"{r.vol_spike:.2f}",
                "news": self._format_news_cell(r),
                "notes": r.notes,
            }
            iid = tree.insert("", "end", values=tuple(colmap.get(c, "") for c in cols), tags=tuple(tags))
            self._row_meta[(id(tree), iid)] = {"symbol": r.symbol, "news_url": getattr(r, "news_url", "") or "", "headline": getattr(r, "news_headline", "") or ""}


    def _fill_gappers_tree(self, tree: ttk.Treeview, rows: List[Gapper]):
        tree.delete(*tree.get_children())
        tid = id(tree)
        self._row_meta = {k: v for k, v in self._row_meta.items() if k[0] != tid}
        cols = tuple(tree["columns"])
        for r in rows:
            tag = "gapper_aplus" if "A+G (" in (r.notes or "") else "gapper"
            colmap = {
                "symbol": r.symbol,
                "gap_pct": f"{r.gap_pct:.2f}",
                "ready": (f"YES: {r.ready_reason}" if getattr(r, "ready", 0) else ""),
                "last": f"{r.last:.2f}",
                "prev_close": f"{r.prev_close:.2f}",
                "session_vol": str(r.session_vol),
                "score": f"{r.score:.2f}",
                "universe": r.universe,
                "news": self._format_news_cell(r),
                "notes": r.notes,
            }
            iid = tree.insert("", "end", values=tuple(colmap.get(c, "") for c in cols), tags=(tag,))
            self._row_meta[(id(tree), iid)] = {"symbol": r.symbol, "news_url": getattr(r, "news_url", "") or "", "headline": getattr(r, "news_headline", "") or ""}



def main():
    cfg = ScannerConfig()

    missing = []
    if not os.getenv("ALPACA_API_KEY"):
        missing.append("ALPACA_API_KEY")
    if not os.getenv("ALPACA_API_SECRET"):
        missing.append("ALPACA_API_SECRET")
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")

    ub = UniverseBuilder()
    uni = ub.load_or_build(force=False)

    # Inject universes into cfg (dataclass is frozen, so rebuild)
    cfg = ScannerConfig(
        poll_seconds=cfg.poll_seconds,
        bars_limit=cfg.bars_limit,
        skip_bars_when_closed=cfg.skip_bars_when_closed,

        aplus_min_persistence=cfg.aplus_min_persistence,
        aplus_min_vol_spike=cfg.aplus_min_vol_spike,
        aplus_min_atr_pct=cfg.aplus_min_atr_pct,
        aplus_max_atr_pct=cfg.aplus_max_atr_pct,

        export_dir=cfg.export_dir,
        export_auto=cfg.export_auto,
        export_write_json=cfg.export_write_json,
        export_write_csv=cfg.export_write_csv,
        export_top_n=cfg.export_top_n,

        normal_universe=tuple(uni.normal),
        penny_universe=tuple(uni.penny),

        normal_min_price=cfg.normal_min_price,
        normal_max_price=cfg.normal_max_price,
        normal_min_bar_vol=cfg.normal_min_bar_vol,
        normal_min_atr_pct=cfg.normal_min_atr_pct,
        normal_min_score=cfg.normal_min_score,

        penny_min_price=cfg.penny_min_price,
        penny_max_price=cfg.penny_max_price,
        penny_min_bar_vol=cfg.penny_min_bar_vol,
        penny_min_atr_pct=cfg.penny_min_atr_pct,
        penny_min_score=cfg.penny_min_score,

        gappers_top_n=cfg.gappers_top_n,
        pm_gap_min_pct=cfg.pm_gap_min_pct,
        pm_min_volume=cfg.pm_min_volume,

        gapplus_min_persistence=cfg.gapplus_min_persistence,
        gapplus_min_gap_pct=cfg.gapplus_min_gap_pct,
        gapplus_min_volume=cfg.gapplus_min_volume,
        gapplus_min_price=cfg.gapplus_min_price,
        gapplus_max_price=cfg.gapplus_max_price,

        scan_batch_size=cfg.scan_batch_size,
        prefilter_top_k=cfg.prefilter_top_k,
        max_bars_symbols_per_tick=cfg.max_bars_symbols_per_tick,

        show_snapshot_movers_when_closed=cfg.show_snapshot_movers_when_closed,
        snap_min_move_pct_normal=cfg.snap_min_move_pct_normal,
        snap_min_move_pct_penny=cfg.snap_min_move_pct_penny,
        snap_min_volume_normal=cfg.snap_min_volume_normal,
        snap_min_volume_penny=cfg.snap_min_volume_penny,
    )

    print(f"[Universe] asof={uni.asof_utc} normal={len(uni.normal)} penny={len(uni.penny)} meta={json.dumps(uni.meta, default=str)}")

    root = tk.Tk()
    ScannerGUI(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
