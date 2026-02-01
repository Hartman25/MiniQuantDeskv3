"""
P1 Patch 4 – Periodic State Snapshots

INVARIANT:
    The runtime must periodically persist a SystemStateSnapshot to disk
    so that the RecoveryCoordinator can restore state after a crash.

WHY THIS MATTERS:
    StatePersistence.save_state() exists but is NEVER called in the
    runtime loop.  After a crash, there is no saved state for recovery.

DESIGN:
    - New helper `_build_state_snapshot(position_store, protective_stop_ids)`
      that builds a `SystemStateSnapshot` from current runtime state.
    - The runtime loop calls `StatePersistence.save_state()` after each
      successful cycle (or every N cycles based on env var).
    - Tests validate snapshot construction, not the full runtime integration.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path


class TestBuildStateSnapshot:
    """Test the snapshot builder helper."""

    def test_builds_snapshot_from_position_store(self, tmp_path):
        from core.state.position_store import PositionStore, Position
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        ps.upsert(Position(
            symbol="SPY",
            quantity=Decimal("10"),
            entry_price=Decimal("450.00"),
            entry_time=datetime(2026, 1, 30, 10, 0, tzinfo=timezone.utc),
            strategy="TestStrat",
            order_id="ORD-001",
            stop_loss=Decimal("445.00"),
        ))

        snapshot = build_state_snapshot(position_store=ps)

        assert len(snapshot.positions) == 1
        assert snapshot.positions[0].symbol == "SPY"
        assert snapshot.positions[0].quantity == Decimal("10")
        assert snapshot.current_position_count == 1
        ps.close()

    def test_builds_snapshot_empty_store(self, tmp_path):
        from core.state.position_store import PositionStore
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        snapshot = build_state_snapshot(position_store=ps)

        assert len(snapshot.positions) == 0
        assert snapshot.current_position_count == 0
        ps.close()

    def test_snapshot_includes_pending_orders_from_stop_ids(self, tmp_path):
        from core.state.position_store import PositionStore
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        stop_ids = {"SPY": "BRK-STOP-001", "AAPL": "BRK-STOP-002"}

        snapshot = build_state_snapshot(
            position_store=ps,
            protective_stop_ids=stop_ids,
        )

        assert len(snapshot.pending_orders) == 2
        symbols = {o.symbol for o in snapshot.pending_orders}
        assert symbols == {"SPY", "AAPL"}
        ps.close()


class TestStatePersistenceRoundTrip:
    """Test save + load round-trip."""

    def test_save_and_load(self, tmp_path):
        from core.state.position_store import PositionStore, Position
        from core.runtime.state_snapshot import build_state_snapshot
        from core.recovery.persistence import StatePersistence

        ps = PositionStore(db_path=tmp_path / "pos.db")
        ps.upsert(Position(
            symbol="QQQ",
            quantity=Decimal("5"),
            entry_price=Decimal("380.00"),
            entry_time=datetime(2026, 1, 30, 12, 0, tzinfo=timezone.utc),
            strategy="TestStrat",
            order_id="ORD-002",
        ))

        snapshot = build_state_snapshot(position_store=ps)
        persistence = StatePersistence(state_dir=tmp_path / "state")

        ok = persistence.save_state(snapshot)
        assert ok is True

        loaded = persistence.load_latest_state()
        assert loaded is not None
        assert len(loaded.positions) == 1
        assert loaded.positions[0].symbol == "QQQ"
        assert loaded.positions[0].quantity == Decimal("5")
        ps.close()
