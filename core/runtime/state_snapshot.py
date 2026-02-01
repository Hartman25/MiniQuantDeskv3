"""
P1 Patch 4: Build SystemStateSnapshot from live runtime state.

This bridges the runtime's in-memory state with the persistence layer
so that periodic snapshots can be saved to disk for crash recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

from core.recovery.persistence import (
    SystemStateSnapshot,
    PositionSnapshot,
    OrderSnapshot,
)


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
