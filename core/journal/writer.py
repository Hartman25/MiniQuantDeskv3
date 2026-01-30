"""
PATCH 5: Unified Journal (JSONL)

Purpose:
- Single source of truth for execution + risk + protections.
- Append-only daily JSONL files (one line per event).
- Optional monthly roll-up helper.

Design notes:
- Keep this dependency-light (std lib only).
- Events are dicts; we enrich with timestamps automatically.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class JournalPaths:
    base_dir: Path

    def daily_path(self, day_utc: str) -> Path:
        # day_utc: YYYY-MM-DD
        return self.base_dir / "daily" / f"{day_utc}.jsonl"

    def monthly_path(self, month_utc: str) -> Path:
        # month_utc: YYYY-MM
        return self.base_dir / "monthly" / f"{month_utc}.jsonl"


class JournalWriter:
    """
    Thread-safe JSONL writer with date-based routing.
    """

    def __init__(self, base_dir: Path):
        self._paths = JournalPaths(base_dir=Path(base_dir))
        self._lock = threading.Lock()
        self._paths.base_dir.mkdir(parents=True, exist_ok=True)
        (self._paths.base_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self._paths.base_dir / "monthly").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def write_event(self, event: Dict[str, Any]) -> None:
        """
        Append a single JSON line event to today's daily file.

        Automatically adds:
          - ts (ISO 8601, UTC)
          - day (YYYY-MM-DD)
          - month (YYYY-MM)
        """
        now = self._utc_now()
        day = now.date().isoformat()
        month = f"{now.year:04d}-{now.month:02d}"

        enriched = dict(event)
        enriched.setdefault("ts", now.isoformat())
        enriched.setdefault("day", day)
        enriched.setdefault("month", month)

        line = json.dumps(enriched, default=str, ensure_ascii=False)

        path = self._paths.daily_path(day)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def rollup_month(self, month_utc: str) -> Path:
        """
        Concatenate all daily files for a month into monthly/<YYYY-MM>.jsonl.
        Idempotent (rebuilds file).

        month_utc: 'YYYY-MM'
        """
        daily_dir = self._paths.base_dir / "daily"
        out = self._paths.monthly_path(month_utc)
        out.parent.mkdir(parents=True, exist_ok=True)

        prefix = month_utc + "-"
        daily_files = sorted([p for p in daily_dir.glob(f"{prefix}*.jsonl") if p.is_file()])

        with self._lock:
            with out.open("w", encoding="utf-8") as wf:
                for p in daily_files:
                    wf.write(p.read_text(encoding="utf-8"))

        return out
