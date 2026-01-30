from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class SignalOutcomeLogger:
    """
    Appends trade outcomes keyed to scanner signals.
    This is Phase-4 ML gold.
    """

    def __init__(self, path: str = "exports/scanner_outcomes.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_outcome(
        self,
        symbol: str,
        signal_ts_utc: str,
        action: str,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
        mfe: Optional[float] = None,
        mae: Optional[float] = None,
        hold_minutes: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> None:
        """
        action examples:
          - ENTERED
          - SKIPPED
          - EXITED
          - STOPPED
          - TP_HIT
        """

        record: Dict[str, object] = {
            "ts_logged_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "symbol": symbol,
            "signal_ts_utc": signal_ts_utc,
            "action": action,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "mfe": mfe,
            "mae": mae,
            "hold_minutes": hold_minutes,
            "notes": notes,
        }

        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
