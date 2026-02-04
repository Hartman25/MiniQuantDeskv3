# core/journal/trade_journal.py
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Iterator


SCHEMA_VERSION = "1.0.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class TradeIds:
    run_id: str
    trade_id: str


class TradeJournal:
    """
    Canonical trade journal: append-only daily JSONL.

    Path: <base_dir>/trades/YYYY-MM-DD.jsonl

    This is intentionally *trade-focused* and separate from the existing JournalWriter
    (which is system-wide). We wire both: JournalWriter for system events, TradeJournal for trades.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.trades_dir = self.base_dir / "trades"
        _mkdir(self.trades_dir)
        self._lock = threading.Lock()
        self._current_day: Optional[str] = None
        self._fh = None  # type: Optional[Any]

    @staticmethod
    def new_run_id(prefix: str = "run") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    @staticmethod
    def new_trade_id(prefix: str = "t") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def close(self) -> None:
        with self._lock:
            if self._fh:
                try:
                    self._fh.flush()
                finally:
                    self._fh.close()
                self._fh = None
            self._current_day = None

    def _ensure_open(self, ts_utc: str) -> None:
        day = ts_utc[:10]  # YYYY-MM-DD
        if self._fh is not None and self._current_day == day:
            return

        if self._fh:
            try:
                self._fh.flush()
            finally:
                self._fh.close()

        _mkdir(self.trades_dir)
        path = self.trades_dir / f"{day}.jsonl"
        self._fh = open(path, "a", encoding="utf-8")
        self._current_day = day


    def _validate_event(self, event: Dict[str, Any]) -> None:
        # Patch 2: enforce joinable correlation IDs for every TradeJournal event.
        et = event.get("event_type")
        if not et:
            raise ValueError("TradeJournal event missing required field: event_type")

        missing = [k for k in ("trade_id", "internal_order_id") if not event.get(k)]
        if missing:
            raise ValueError(f"TradeJournal event missing required correlation fields: {missing}. event_type={et}")

    def emit(self, event: Dict[str, Any]) -> None:
        """
        Write one event line. Enforces:
          - schema_version + ts_utc
          - Patch 2 correlation IDs: trade_id + internal_order_id + event_type
        """
        e = dict(event)
        e["schema_version"] = SCHEMA_VERSION
        e.setdefault("ts_utc", utc_now_iso())

        self._validate_event(e)

        line = _compact_json(e)

        with self._lock:
            self._ensure_open(e["ts_utc"])
            assert self._fh is not None
            self._fh.write(line + "\n")
            self._fh.flush()

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """Iterate all journaled trade events (across daily JSONL files)."""
        if not self.trades_dir.exists():
            return iter(())

        def _gen() -> Iterator[Dict[str, Any]]:
            for path in sorted(self.trades_dir.glob("*.jsonl")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                yield json.loads(line)
                            except Exception:
                                continue
                except FileNotFoundError:
                    continue

        return _gen()



def build_trade_event(
    *,
    event_type: str,
    ids: TradeIds,
    internal_order_id: str,
    broker_order_id: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    qty: Optional[str] = None,
    order_type: Optional[str] = None,
    limit_price: Optional[str] = None,
    stop_price: Optional[str] = None,
    strategy: Optional[str] = None,
    reason: Optional[Dict[str, Any]] = None,
    risk: Optional[Dict[str, Any]] = None,
    exchange_ts_utc: Optional[str] = None,
    latency_ms: Optional[int] = None,
    error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "run_id": ids.run_id,
        "trade_id": ids.trade_id,
        "internal_order_id": internal_order_id,
        "broker_order_id": broker_order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "order_type": order_type,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "strategy": strategy,
        "reason": reason,
        "risk": risk,
        "exchange_ts_utc": exchange_ts_utc,
        "latency_ms": latency_ms,
        "local_ts_utc": utc_now_iso(),
        "error": error,
    }

