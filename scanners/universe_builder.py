"""
Universe Builder for Standalone Scanner

Builds two universes:
- NORMAL: liquid-ish, tradable US equities (price bucket)
- PENNY: scan-only bucket (price bucket)

Data sources:
- Alpaca Trading API: /v2/assets  (tradable equities list)
- Alpaca Market Data API: /v2/stocks/snapshots (prices + volumes)

Caching:
- Writes JSON to .cache/scanner_universe.json (or env override)
- Refreshes once per day unless forced

ENV:
  ALPACA_API_KEY
  ALPACA_API_SECRET
  ALPACA_TRADING_BASE_URL (default https://paper-api.alpaca.markets)
  ALPACA_DATA_BASE_URL (default https://data.alpaca.markets)

  UNIVERSE_CACHE_PATH (default .cache/scanner_universe.json)
  UNIVERSE_REFRESH_DAILY (default 1)

  NORMAL_MIN_PRICE (default 5)
  NORMAL_MAX_PRICE (default 500)
  PENNY_MIN_PRICE (default 1)
  PENNY_MAX_PRICE (default 5)

  NORMAL_MIN_PREV_DAY_VOL (default 1000000)   # proxy liquidity filter
  PENNY_MIN_PREV_DAY_VOL (default 5000000)    # pennies need crazy volume to be viable
"""

from __future__ import annotations

import json
import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import math


# ----------------------------
# Helpers
# ----------------------------

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(float(v))
    except Exception:
        return default

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _today_utc_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class UniverseResult:
    asof_utc: str
    date_utc: str
    normal: List[str]
    penny: List[str]
    meta: Dict[str, object]


class UniverseBuilder:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "").strip()
        self.api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_API_SECRET in environment.")

        self.trading_base = os.getenv("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
        self.data_base = os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")

        self.cache_path = Path(os.getenv("UNIVERSE_CACHE_PATH", ".cache/scanner_universe.json"))
        self.refresh_daily = _env_int("UNIVERSE_REFRESH_DAILY", 1) == 1
        self.cache_max_age_hours = _env_int("UNIVERSE_CACHE_MAX_AGE_HOURS", 20)
        self.cache_bust_on_filter_change = _env_int("UNIVERSE_CACHE_BUST_ON_FILTER_CHANGE", 1) == 1

        self.normal_min_price = _env_float("NORMAL_MIN_PRICE", 5.0)
        self.normal_max_price = _env_float("NORMAL_MAX_PRICE", 500.0)
        self.penny_min_price = _env_float("PENNY_MIN_PRICE", 1.0)
        self.penny_max_price = _env_float("PENNY_MAX_PRICE", 5.0)

        self.normal_min_prev_day_vol = _env_int("NORMAL_MIN_PREV_DAY_VOL", 1_000_000)
        self.penny_min_prev_day_vol = _env_int("PENNY_MIN_PREV_DAY_VOL", 5_000_000)

        # Exchanges you actually want (no OTC)
        self.allowed_exchanges = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "IEX"}

    def _filters_signature(self) -> str:
        """Stable signature of universe-building knobs."""
        payload = {
            "normal_min_price": self.normal_min_price,
            "normal_max_price": self.normal_max_price,
            "penny_min_price": self.penny_min_price,
            "penny_max_price": self.penny_max_price,
            "normal_min_prev_day_vol": self.normal_min_prev_day_vol,
            "penny_min_prev_day_vol": self.penny_min_prev_day_vol,
            "allowed_exchanges": sorted(self.allowed_exchanges),
        }
        s = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    # ----------------------------
    # Cache
    # ----------------------------

    def load_or_build(self, force: bool = False) -> UniverseResult:
        """
        Loads cached universe unless:
        - force=True
        - cache missing/invalid
        - cache is stale (daily refresh enabled)
        - cache is EMPTY (normal+penny == 0) -> auto rebuild (common during early bring-up)
        """
        if not force:
            cached = self._try_load_cache()
            if cached:
                # Auto-heal: empty cache is not useful; rebuild.
                if (len(cached.normal) == 0) and (len(cached.penny) == 0):
                    # overwrite bad/empty cache
                    res = self.build()
                    self._write_cache(res)
                    return res
                return cached

        res = self.build()
        self._write_cache(res)
        return res

    def _try_load_cache(self) -> Optional[UniverseResult]:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))

            # Age-based staleness
            asof_utc = (data.get("asof_utc") or "").strip()
            if asof_utc:
                s = asof_utc.replace("Z", "+00:00") if asof_utc.endswith("Z") else asof_utc
                try:
                    asof_dt = datetime.fromisoformat(s).astimezone(timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - asof_dt).total_seconds() / 3600.0
                    if age_hours > float(self.cache_max_age_hours):
                        return None
                except Exception:
                    return None

            # Daily refresh (UTC date)
            date_utc = data.get("date_utc")
            if self.refresh_daily and date_utc != _today_utc_ymd():
                return None

            # Bust cache if knobs changed
            if self.cache_bust_on_filter_change:
                cached_sig = (data.get("filters_sig") or "").strip()
                current_sig = self._filters_signature()
                if not cached_sig:
                    return None
                if cached_sig != current_sig:
                    return None

            return UniverseResult(
                asof_utc=data.get("asof_utc", ""),
                date_utc=data.get("date_utc", ""),
                normal=list(data.get("normal", [])),
                penny=list(data.get("penny", [])),
                meta=dict(data.get("meta", {})),
            )
        except Exception:
            return None


    def _write_cache(self, res: UniverseResult) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "asof_utc": res.asof_utc,
            "date_utc": res.date_utc,
            "filters_sig": self._filters_signature(),
            "normal": res.normal,
            "penny": res.penny,
            "meta": res.meta,
        }
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


    def build(self) -> UniverseResult:
        t0 = time.time()
        assets = self._fetch_assets()
        symbols = self._filter_assets_to_symbols(assets)
        snapshots = self._fetch_snapshots_batched(symbols, batch_size=900)

        normal: List[str] = []
        penny: List[str] = []

        # Diagnostics
        px_ok = 0
        vol_ok = 0
        in_penny_px = 0
        in_normal_px = 0
        sample_printed = 0

        def _vol_ok(vol: Optional[int], threshold: int) -> bool:
            # permissive for now; if Alpaca returns None volumes for many symbols,
            # we do not want to zero-out the universe during bring-up.
            if vol is None:
                return True
            return vol >= threshold

        for sym in symbols:
            snap = snapshots.get(sym)
            px, prev_vol = self._snapshot_price_and_prev_vol(snap)

            if px is None:
                continue

            px_ok += 1
            if _vol_ok(prev_vol, 0):
                vol_ok += 1

            # Print a few samples so we can see what px/vol look like in reality
            if sample_printed < 5:
                try:
                    pb = (snap or {}).get("prevDailyBar") or {}
                    db = (snap or {}).get("dailyBar") or {}
                    lt = (snap or {}).get("latestTrade") or {}
                    lq = (snap or {}).get("latestQuote") or {}
                    print(
                        f"[UniverseBuilder] sample {sym}: px={px} prev_vol={prev_vol} "
                        f"prevDailyBar.c={pb.get('c')} prevDailyBar.v={pb.get('v')} "
                        f"dailyBar.c={db.get('c')} dailyBar.v={db.get('v')} "
                        f"latestTrade.p={lt.get('p')} latestQuote.bp={lq.get('bp')} ap={lq.get('ap')}"
                    )
                    sample_printed += 1
                except Exception:
                    pass

            # Price buckets
            if self.penny_min_price <= px <= self.penny_max_price:
                in_penny_px += 1
                if _vol_ok(prev_vol, self.penny_min_prev_day_vol):
                    penny.append(sym)
                continue

            if self.normal_min_price <= px <= self.normal_max_price:
                in_normal_px += 1
                if _vol_ok(prev_vol, self.normal_min_prev_day_vol):
                    normal.append(sym)

        # Extra diagnostics
        print(
            f"[UniverseBuilder] diag: px_ok={px_ok} "
            f"in_normal_px={in_normal_px} in_penny_px={in_penny_px} "
            f"normal_selected={len(normal)} penny_selected={len(penny)}"
        )

        normal.sort()
        penny.sort()

        meta = {
            "assets_total": len(assets),
            "symbols_total": len(symbols),
            "normal_count": len(normal),
            "penny_count": len(penny),
            "build_seconds": round(time.time() - t0, 3),
            "filters": {
                "normal_price": [self.normal_min_price, self.normal_max_price],
                "penny_price": [self.penny_min_price, self.penny_max_price],
                "normal_min_prev_day_vol": self.normal_min_prev_day_vol,
                "penny_min_prev_day_vol": self.penny_min_prev_day_vol,
            },
            "data_feed": "iex",
            "diag": {
                "px_ok": px_ok,
                "in_normal_px": in_normal_px,
                "in_penny_px": in_penny_px,
            },
        }

        # Debug: show a tiny sample of snapshot keys for sanity (no secrets)
        try:
            sample = next(iter(snapshots.items()))
            sym0, snap0 = sample
            print(f"[UniverseBuilder] sample_symbol={sym0} snapshot_keys={list((snap0 or {}).keys())}")
        except Exception:
            pass

        return UniverseResult(
            asof_utc=_utc_now_iso(),
            date_utc=_today_utc_ymd(),
            normal=normal,
            penny=penny,
            meta=meta,
        )

    def _fetch_assets(self) -> List[dict]:
        url = f"{self.trading_base}/v2/assets"
        params = {"status": "active", "asset_class": "us_equity"}

        # Debug (no secrets)
        key_ok = bool(self.api_key)
        secret_ok = bool(self.api_secret)
        print(f"[UniverseBuilder] trading_base={self.trading_base} key_set={key_ok} secret_set={secret_ok}")

        r = requests.get(url, headers=self._headers(), params=params, timeout=20)

        if r.status_code != 200:
            # more useful debug without leaking
            req_id = r.headers.get("x-request-id") or r.headers.get("X-Request-Id") or ""
            raise RuntimeError(
                f"Alpaca assets error {r.status_code}: {r.text[:300]} | request_id={req_id} | url={url}"
            )

        return r.json()

    def _filter_assets_to_symbols(self, assets: List[dict]) -> List[str]:
        out: List[str] = []
        for a in assets:
            sym = (a.get("symbol") or "").strip().upper()
            if not sym:
                continue

            if not a.get("tradable", False):
                continue

            # Ban weird stuff / non-common shares
            if "." in sym or "/" in sym:
                continue

            exch = (a.get("exchange") or "").strip().upper()
            if exch and exch not in self.allowed_exchanges:
                continue

            # Some assets are ETFs etc. That’s fine for scanner.
            out.append(sym)

        # De-dupe
        return sorted(set(out))

    def _fetch_snapshots_batched(self, symbols: List[str], batch_size: int = 300) -> Dict[str, dict]:
        """
        Multi-snapshot endpoint response shape varies in the wild:
        A) {"snapshots": {"AAPL": {...}, "MSFT": {...}}}
        B) {"AAPL": {...}, "MSFT": {...}}  (dict keyed by symbol)
        We handle both.

        Also: smaller batch_size reduces URL length risk and weird empty responses.
        """
        out: Dict[str, dict] = {}
        if not symbols:
            return out

        # Normalize base URL: strip trailing / and also strip a trailing "/v2" if user set it that way
        base = (self.data_base or "").rstrip("/")
        if base.endswith("/v2"):
            base = base[:-3]

        url = f"{base}/v2/stocks/snapshots"
        headers = self._headers()

        total_returned = 0

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            params = {"symbols": ",".join(batch), "feed": "iex"}
            r = requests.get(url, headers=headers, params=params, timeout=30)

            if r.status_code != 200:
                req_id = r.headers.get("x-request-id") or r.headers.get("X-Request-Id") or ""
                raise RuntimeError(
                    f"Alpaca snapshots error {r.status_code}: {r.text[:300]} | request_id={req_id} | url={url}"
                )

            data = r.json() or {}

            # Shape A
            snaps = data.get("snapshots")
            if isinstance(snaps, dict):
                batch_snaps = snaps
            # Shape B (dict keyed by symbol)
            elif isinstance(data, dict):
                # Filter to only symbol keys in this batch, ignoring any metadata keys if present
                batch_set = set(batch)
                batch_snaps = {k: v for k, v in data.items() if k in batch_set and isinstance(v, dict)}
            else:
                batch_snaps = {}

            total_returned += len(batch_snaps)

            for sym, snap in batch_snaps.items():
                out[sym] = snap

        # Debug (safe)
        if out:
            sym0, snap0 = next(iter(out.items()))
            print(f"[UniverseBuilder] snapshots_returned={total_returned} unique={len(out)} sample_symbol={sym0} keys={list((snap0 or {}).keys())}")
        else:
            print("[UniverseBuilder] snapshots_returned=0 (empty). Check data plan/feed/base URL.")

        return out

    def _snapshot_price_and_prev_vol(self, snap: Optional[dict]) -> Tuple[Optional[float], Optional[int]]:
        """
        Robust snapshot parsing across Alpaca variations.

        Price candidates (in order):
        - latestTrade.p
        - latestQuote.ap / bp midpoint
        - minuteBar.c
        - dailyBar.c
        - prevDailyBar.c

        Volume proxy (in order):
        - dailyBar.v
        - prevDailyBar.v
        """
        if not snap or not isinstance(snap, dict):
            return None, None

        def _get(path: List[str]):
            cur = snap
            for k in path:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(k)
            return cur

        # Try to compute midpoint from latestQuote if present
        def _midpoint() -> Optional[float]:
            aq = _get(["latestQuote", "ap"])
            bq = _get(["latestQuote", "bp"])
            try:
                if aq is None or bq is None:
                    return None
                aqf = float(aq)
                bqf = float(bq)
                if aqf > 0 and bqf > 0:
                    return (aqf + bqf) / 2.0
            except Exception:
                return None
            return None

        price_candidates = [
            _get(["latestTrade", "p"]),
            _midpoint(),
            _get(["minuteBar", "c"]),
            _get(["dailyBar", "c"]),
            _get(["prevDailyBar", "c"]),
        ]

        px: Optional[float] = None
        for x in price_candidates:
            try:
                if x is None:
                    continue
                fx = float(x)
                if fx > 0 and math.isfinite(fx):
                    px = fx
                    break
            except Exception:
                continue

        vol_candidates = [
            _get(["dailyBar", "v"]),
            _get(["prevDailyBar", "v"]),
        ]

        pv: Optional[int] = None
        for x in vol_candidates:
            try:
                if x is None:
                    continue
                iv = int(float(x))
                if iv >= 0:
                    pv = iv
                    break
            except Exception:
                continue

        return px, pv