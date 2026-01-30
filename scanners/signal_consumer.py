from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ScannerSignal:
    ts_utc: str
    symbol: str
    mode: str
    session: str
    universe: str
    score: Optional[float] = None
    gap_pct: Optional[float] = None
    session_vol: Optional[int] = None
    last: Optional[float] = None
    vwap: Optional[float] = None
    ema9: Optional[float] = None
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    atr_pct: Optional[float] = None
    vol_spike: Optional[float] = None
    persistence: Optional[int] = None
    catalyst: Optional[str] = None
    risk_flags: Tuple[str, ...] = ()
    ready: Optional[bool] = None
    ready_reason: Optional[str] = None
    headline: Optional[str] = None
    news_url: Optional[str] = None
    source: Optional[str] = None

    @property
    def key_day(self) -> str:
        """
        Day key in UTC based on ts_utc. If ts_utc isn't parseable, fallback to today's UTC date.
        """
        try:
            dt = _parse_ts_utc(self.ts_utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @property
    def dedupe_key(self) -> str:
        """
        Stable dedupe key for "one signal per symbol per day per session".
        """
        # session might be PRE/REG/POST/CLOSED; keep it
        sess = (self.session or "").upper().strip() or "UNK"
        return f"{self.key_day}|{self.symbol.upper().strip()}|{sess}"


def _parse_ts_utc(s: str) -> datetime:
    # Accepts "2026-01-25T07:12:33Z" or ISO8601 with offset
    s = (s or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _normalize_signal(d: Dict[str, Any]) -> Optional[ScannerSignal]:
    """
    Normalize a JSON dict from scanner_signals.jsonl into ScannerSignal.
    Returns None if required fields are missing.
    Required: ts_utc, symbol, mode, session, universe
    """
    ts_utc = (d.get("ts_utc") or d.get("ts") or "").strip()
    symbol = (d.get("symbol") or "").strip().upper()
    mode = (d.get("mode") or "").strip().upper()
    session = (d.get("session") or "").strip().upper()
    universe = (d.get("universe") or "").strip().upper()

    if not ts_utc or not symbol or not session or not universe:
        return None

    risk = d.get("risk_flags") or d.get("risk") or []
    if isinstance(risk, str):
        risk = [risk]
    risk_flags = tuple(str(x).strip().upper() for x in risk if str(x).strip())

    return ScannerSignal(
        ts_utc=ts_utc,
        symbol=symbol,
        mode=mode or "AUTO",
        session=session,
        universe=universe,
        score=_safe_float(d.get("score")),
        gap_pct=_safe_float(d.get("gap_pct")),
        session_vol=_safe_int(d.get("session_vol")),
        last=_safe_float(d.get("last")),
        vwap=_safe_float(d.get("vwap")),
        ema9=_safe_float(d.get("ema9")),
        ema20=_safe_float(d.get("ema20")),
        ema50=_safe_float(d.get("ema50")),
        atr_pct=_safe_float(d.get("atr_pct")),
        vol_spike=_safe_float(d.get("vol_spike")),
        persistence=_safe_int(d.get("persistence")),
        catalyst=(str(d.get("catalyst")).strip().upper() if d.get("catalyst") else None),
        risk_flags=risk_flags,
        ready=bool(d.get("ready")) if d.get("ready") is not None else None,
        ready_reason=(str(d.get("ready_reason")).strip() if d.get("ready_reason") else None),
        headline=(str(d.get("headline")).strip() if d.get("headline") else None),
        news_url=(str(d.get("news_url")).strip() if d.get("news_url") else None),
        source=(str(d.get("source")).strip() if d.get("source") else None),
    )


class ScannerSignalConsumer:
    """
    Reads scanner JSONL signals and produces deduped candidates.
    - Tracks file offsets (so you only process new lines)
    - Dedupes "symbol/day/session" using SQLite (persistent across restarts)
    """

    def __init__(
        self,
        signals_path: str = "exports/scanner_signals.jsonl",
        state_db_path: str = ".cache/scanner_signal_consumer.sqlite",
        allow_penny: bool = False,
        require_ready: bool = True,
        veto_risk_flags: Tuple[str, ...] = ("DILUTION", "REVERSE_SPLIT"),
        max_age_minutes: int = 240,
    ) -> None:
        self.signals_path = Path(signals_path)
        self.state_db_path = Path(state_db_path)
        self.allow_penny = allow_penny
        self.require_ready = require_ready
        self.veto_risk_flags = tuple(x.upper() for x in veto_risk_flags)
        self.max_age_minutes = max_age_minutes

        self.state_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.state_db_path.as_posix(), check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS consumer_state (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_signals (
              dedupe_key TEXT PRIMARY KEY,
              first_seen_ts_utc TEXT NOT NULL,
              symbol TEXT NOT NULL,
              session TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _get_state(self, k: str, default: str) -> str:
        cur = self._conn.cursor()
        cur.execute("SELECT v FROM consumer_state WHERE k=?", (k,))
        row = cur.fetchone()
        return row[0] if row else default

    def _set_state(self, k: str, v: str) -> None:
        cur = self._conn.cursor()
        cur.execute("INSERT INTO consumer_state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self._conn.commit()

    def _mark_seen(self, s: ScannerSignal) -> bool:
        """
        Returns True if newly seen, False if duplicate.
        """
        cur = self._conn.cursor()
        try:
            cur.execute(
                "INSERT INTO seen_signals(dedupe_key, first_seen_ts_utc, symbol, session) VALUES(?,?,?,?)",
                (s.dedupe_key, s.ts_utc, s.symbol, s.session),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def _is_acceptable(self, s: ScannerSignal) -> bool:
        # Age gating (prevents your bot trading stale signals)
        try:
            dt = _parse_ts_utc(s.ts_utc)
            age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
            if age_min > float(self.max_age_minutes):
                return False
        except Exception:
            # if unparseable, treat as stale
            return False

        # Penny gate
        if (s.universe or "").upper() == "PENNY" and not self.allow_penny:
            return False

        # READY gate
        if self.require_ready and s.ready is not True:
            return False

        # Risk veto
        for rf in s.risk_flags:
            if rf.upper() in self.veto_risk_flags:
                return False

        return True

    def read_new_signals(self, limit: int = 200) -> List[ScannerSignal]:
        """
        Reads newly appended JSONL lines (based on file offset checkpoint).
        """
        if not self.signals_path.exists():
            return []

        offset_key = f"offset::{self.signals_path.resolve().as_posix()}"
        offset_str = self._get_state(offset_key, "0")
        try:
            offset = int(offset_str)
        except Exception:
            offset = 0

        out: List[ScannerSignal] = []
        with self.signals_path.open("r", encoding="utf-8") as f:
            f.seek(offset)
            while len(out) < limit:
                line = f.readline()
                if not line:
                    break
                offset = f.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                s = _normalize_signal(d)
                if not s:
                    continue
                # dedupe persistently
                if not self._mark_seen(s):
                    continue
                # filter for bot usefulness
                if not self._is_acceptable(s):
                    continue
                out.append(s)

        self._set_state(offset_key, str(offset))
        return out

    def get_candidates(self, limit: int = 50, sort: str = "score_desc") -> List[str]:
        """
        Returns a list of symbols suitable for feeding into the bot's strategy selection.
        """
        sigs = self.read_new_signals(limit=500)

        # sort
        if sort == "score_desc":
            sigs.sort(key=lambda s: (s.score if s.score is not None else -1e9), reverse=True)
        elif sort == "time_desc":
            sigs.sort(key=lambda s: s.ts_utc, reverse=True)

        # unique symbol order-preserving
        seen = set()
        symbols: List[str] = []
        for s in sigs:
            if s.symbol in seen:
                continue
            seen.add(s.symbol)
            symbols.append(s.symbol)
            if len(symbols) >= limit:
                break
        return symbols


def demo_loop() -> None:
    """
    Manual test:
      python -c "from scanners.signal_consumer import demo_loop; demo_loop()"
    """
    c = ScannerSignalConsumer()
    try:
        while True:
            syms = c.get_candidates(limit=15)
            if syms:
                print("[consumer] candidates:", syms)
            time.sleep(5)
    finally:
        c.close()
