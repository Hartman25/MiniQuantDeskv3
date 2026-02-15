"""
P1 Patch 4 + PATCH 5: Build SystemStateSnapshot from live runtime state.

This bridges the runtime's in-memory state with the persistence layer
so that periodic snapshots can be saved to disk for crash recovery.

PATCH 5 adds SnapshotHealthMonitor: tracks consecutive snapshot failures
and triggers halt in live mode after a configurable threshold.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

from core.recovery.persistence import (
    SystemStateSnapshot,
    PositionSnapshot,
    OrderSnapshot,
)

_logger = logging.getLogger(__name__)


def build_state_snapshot(
    position_store=None,
    protective_stop_ids: Optional[Dict[str, str]] = None,
) -> SystemStateSnapshot:
    """
    Build a SystemStateSnapshot from current runtime state.

    Args:
        position_store: PositionStore (SQLite-backed) with open positions.
        protective_stop_ids: {symbol: broker_order_id} of active protective stops.

    Returns:
        A ready-to-persist SystemStateSnapshot.
    """
    positions = []
    if position_store is not None:
        try:
            for pos in (position_store.get_all() if hasattr(position_store, "get_all") else []):
                positions.append(PositionSnapshot(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    avg_price=pos.entry_price,
                    entry_time=pos.entry_time,
                    unrealized_pnl=pos.unrealized_pnl or Decimal("0"),
                    side="LONG",  # system is long-only
                ))
        except Exception:
            pass

    pending_orders = []
    for sym, broker_id in (protective_stop_ids or {}).items():
        pending_orders.append(OrderSnapshot(
            order_id=f"protective-stop-{sym}",
            broker_order_id=broker_id,
            symbol=sym,
            side="SELL",
            quantity=Decimal("0"),  # unknown at snapshot time
            order_type="STOP",
            limit_price=None,
            status="SUBMITTED",
            submitted_at=datetime.now(timezone.utc),
        ))

    return SystemStateSnapshot(
        timestamp=datetime.now(timezone.utc),
        positions=positions,
        pending_orders=pending_orders,
        current_position_count=len(positions),
    )


class SnapshotHealthMonitor:
    """PATCH 5: Track consecutive snapshot failures.

    In LIVE mode, repeated snapshot failures must not be silently ignored.
    After ``max_consecutive_failures`` consecutive failures the monitor
    reports ``is_failed`` so the runtime can halt.

    In PAPER mode the caller should log and continue (soft-fail).
    """

    def __init__(self, max_consecutive_failures: int = 3) -> None:
        self._max = max_consecutive_failures
        self._consecutive_failures: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0

    # -- public API --

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._total_successes += 1

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._total_failures += 1
        _logger.warning(
            "Snapshot save failed (%d/%d consecutive)",
            self._consecutive_failures,
            self._max,
        )

    @property
    def is_failed(self) -> bool:
        """True when consecutive failures have reached the threshold."""
        return self._consecutive_failures >= self._max

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def get_stats(self) -> dict:
        return {
            "consecutive_failures": self._consecutive_failures,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "threshold": self._max,
            "is_failed": self.is_failed,
        }
