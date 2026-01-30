"""
Scanner Output Adapter (Gate 1)

Writes selected candidates to inbox.jsonl for Gate 2 processing.

INTEGRATION:
- Import this module in standalone_scanner.py
- Call write_candidate() for each high-scoring symbol
- Deduplicates within 5 minutes (same symbol, similar score)

FORMAT (inbox.jsonl):
{
  "id": "2026-01-25T18:12:00Z:CRVS:scanner_v2",
  "ts": "2026-01-25T18:12:00Z",
  "symbol": "CRVS",
  "session": "pre",
  "score": 8.7,
  "features": {
    "rvol": 4.2,
    "gap_pct": 18.1,
    "spread_bps": 12,
    "pm_vol": 320000,
    "dollar_vol": 12000000
  },
  "levels": {
    "hold": 25.00,
    "break": 26.49,
    "t1": 30.00,
    "t2": 36.00
  },
  "source": "scanner_v2",
  "version": "2.1"
}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class ScannerOutputAdapter:
    """Writes scanner candidates to inbox.jsonl (Gate 1 output)"""

    def __init__(self, inbox_path: Path):
        self.inbox_path = inbox_path
        self.inbox_path.parent.mkdir(parents=True, exist_ok=True)

        # Track recent writes (dedup within 5 minutes)
        self.recent_writes: Dict[str, datetime] = {}

    def write_candidate(
        self,
        symbol: str,
        score: float,
        session: str = "rth",
        features: Optional[Dict[str, float]] = None,
        levels: Optional[Dict[str, float]] = None,
        source: str = "scanner_v2",
        version: str = "2.1",
    ) -> bool:
        """
        Write candidate to inbox.jsonl.
        
        Args:
            symbol: Ticker symbol
            score: Scanner score (0-10)
            session: "pre" or "rth"
            features: Scanner metrics (rvol, gap_pct, spread_bps, etc.)
            levels: Key levels (hold, break, t1, t2)
            source: Scanner identifier
            version: Schema version
            
        Returns:
            True if written, False if deduplicated
        """
        now = datetime.now(timezone.utc)

        # Check if recently written (within 5 minutes)
        if symbol in self.recent_writes:
            last_write = self.recent_writes[symbol]
            elapsed = (now - last_write).total_seconds()
            if elapsed < 300:  # 5 minutes
                return False

        # Build candidate record
        candidate_id = f"{now.isoformat().replace('+00:00', 'Z')}:{symbol}:{source}"
        
        record = {
            "id": candidate_id,
            "ts": now.isoformat().replace("+00:00", "Z"),
            "symbol": symbol,
            "session": session,
            "score": round(score, 2),
            "features": features or {},
            "levels": levels or {},
            "source": source,
            "version": version,
        }

        # Append to inbox.jsonl
        try:
            with open(self.inbox_path, "a") as f:
                f.write(json.dumps(record) + "\n")

            # Update recent writes
            self.recent_writes[symbol] = now

            return True

        except Exception as e:
            print(f"Failed to write to inbox: {e}")
            return False

    def clear_recent_cache(self) -> None:
        """Clear recent writes cache (for testing)"""
        self.recent_writes.clear()


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def get_scanner_adapter(data_dir: Optional[Path] = None) -> ScannerOutputAdapter:
    """
    Get scanner output adapter with default paths.
    
    Args:
        data_dir: Override data directory (default: MiniQuantDeskv2/data/universe)
        
    Returns:
        ScannerOutputAdapter instance
    """
    if data_dir is None:
        # Default: repo_root/data/universe/inbox.jsonl
        repo_root = Path(__file__).parent.parent.parent
        data_dir = repo_root / "data" / "universe"

    inbox_path = data_dir / "inbox.jsonl"
    return ScannerOutputAdapter(inbox_path)
