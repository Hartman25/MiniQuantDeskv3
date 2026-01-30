"""
Patch 21: Build daily training snapshots from scanner signals + outcomes.

Inputs (append-only):
  - exports/scanner_signals.jsonl
  - exports/scanner_outcomes.jsonl

Output (daily):
  - exports/training/daily/YYYY-MM-DD.csv

Merge rule (simple + robust):
  - For each outcome event, link it to the most recent prior signal for the same symbol
    within LINK_WINDOW_MINUTES (default 360).
  - Features come from the signal record at decision time.
  - Label comes from outcome pnl (pnl > 0 => 1, pnl <= 0 => 0), plus raw pnl.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


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


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def build_daily_snapshots(
    signals_path: str = "exports/scanner_signals.jsonl",
    outcomes_path: str = "exports/scanner_outcomes.jsonl",
    out_dir: str = "exports/training/daily",
    link_window_minutes: int = 360,
) -> List[str]:
    sig_p = Path(signals_path)
    out_p = Path(outcomes_path)
    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)

    signals = _read_jsonl(sig_p)
    outcomes = _read_jsonl(out_p)

    # index signals by symbol, sorted by time
    sig_by_sym: Dict[str, List[Tuple[datetime, Dict[str, Any]]]] = {}
    for s in signals:
        sym = str(s.get("symbol") or "").upper().strip()
        dt = _parse_ts(str(s.get("ts_utc") or ""))
        if not sym or not dt:
            continue
        sig_by_sym.setdefault(sym, []).append((dt, s))

    for sym in sig_by_sym:
        sig_by_sym[sym].sort(key=lambda x: x[0])

    produced: Dict[str, List[Dict[str, Any]]] = {}

    for o in outcomes:
        sym = str(o.get("symbol") or "").upper().strip()
        dt_o = _parse_ts(str(o.get("ts_logged_utc") or o.get("ts_utc") or ""))
        if not sym or not dt_o:
            continue

        # find latest prior signal within window
        cands = sig_by_sym.get(sym, [])
        if not cands:
            continue

        best: Optional[Tuple[datetime, Dict[str, Any]]] = None
        for dt_s, srec in reversed(cands):
            if dt_s > dt_o:
                continue
            delta_min = (dt_o - dt_s).total_seconds() / 60.0
            if delta_min <= link_window_minutes:
                best = (dt_s, srec)
                break
            # too old; can break because list is sorted
            if dt_s < dt_o:
                break

        if not best:
            continue

        dt_s, srec = best

        pnl = _safe_float(o.get("pnl"))
        y = None
        if pnl is not None:
            y = 1 if pnl > 0 else 0

        day = _day_key(dt_s)

        row = {
            # identifiers
            "day": day,
            "symbol": sym,
            "signal_ts_utc": dt_s.isoformat().replace("+00:00", "Z"),
            "outcome_ts_utc": dt_o.isoformat().replace("+00:00", "Z"),
            "action": str(o.get("action") or ""),
            # label
            "pnl": pnl,
            "y_win": y,
            # core features (keep stable)
            "session": str(srec.get("session") or ""),
            "universe": str(srec.get("universe") or ""),
            "score": _safe_float(srec.get("score")),
            "gap_pct": _safe_float(srec.get("gap_pct")),
            "session_vol": _safe_int(srec.get("session_vol")),
            "last": _safe_float(srec.get("last")),
            "vwap": _safe_float(srec.get("vwap")),
            "ema9": _safe_float(srec.get("ema9")),
            "ema20": _safe_float(srec.get("ema20")),
            "ema50": _safe_float(srec.get("ema50")),
            "atr_pct": _safe_float(srec.get("atr_pct")),
            "vol_spike": _safe_float(srec.get("vol_spike")),
            "persistence": _safe_int(srec.get("persistence")),
            # categorical context
            "catalyst": str(srec.get("catalyst") or ""),
            "risk_flags": "|".join(list(srec.get("risk_flags") or [])),
            "ready": srec.get("ready"),
        }

        produced.setdefault(day, []).append(row)

    written: List[str] = []
    for day, rows in produced.items():
        out_file = out_dir_p / f"{day}.csv"
        # stable column order
        cols = list(rows[0].keys())
        with out_file.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        written.append(str(out_file))

    return sorted(written)


def main() -> None:
    written = build_daily_snapshots(
        signals_path=os.getenv("SIGNALS_JSONL_PATH", "exports/scanner_signals.jsonl"),
        outcomes_path=os.getenv("OUTCOMES_JSONL_PATH", "exports/scanner_outcomes.jsonl"),
        out_dir=os.getenv("TRAINING_OUT_DIR", "exports/training/daily"),
        link_window_minutes=int(float(os.getenv("LINK_WINDOW_MINUTES", "360"))),
    )
    print(f"[training] wrote {len(written)} files")
    for p in written[-5:]:
        print(" -", p)


if __name__ == "__main__":
    main()