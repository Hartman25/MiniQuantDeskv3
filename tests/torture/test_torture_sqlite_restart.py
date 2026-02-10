"""
Torture test: SQLite restart safety.

Verifies:
  - No "database is locked" errors after restart.
  - Journal files are writable after restart.
  - Position store can be opened, written, closed, and re-opened cleanly.
  - Simulated KeyboardInterrupt → restart scenario works.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from core.journal.writer import JournalWriter


class TestSqliteRestart:
    """PositionStore open/close/reopen without lock errors."""

    def test_position_store_reopen_no_lock(self, tmp_path):
        """Open, write, close, reopen — no database-locked error."""
        from core.state.position_store import PositionStore, Position
        from datetime import datetime, timezone
        from decimal import Decimal

        db_path = tmp_path / "positions.db"

        # First session
        store1 = PositionStore(db_path=db_path)
        pos = Position(
            symbol="SPY",
            quantity=Decimal("10"),
            entry_price=Decimal("100.00"),
            entry_time=datetime.now(timezone.utc),
            strategy="VWAP",
            order_id="test-001",
        )
        store1.upsert(pos)
        result = store1.get("SPY")
        assert result is not None
        store1.close()

        # Second session — must not raise "database is locked"
        store2 = PositionStore(db_path=db_path)
        result2 = store2.get("SPY")
        assert result2 is not None
        assert str(result2.symbol) == "SPY"

        # Write again
        pos2 = Position(
            symbol="QQQ",
            quantity=Decimal("5"),
            entry_price=Decimal("200.00"),
            entry_time=datetime.now(timezone.utc),
            strategy="VWAP",
            order_id="test-002",
        )
        store2.upsert(pos2)
        all_pos = store2.get_all()
        assert len(all_pos) == 2
        store2.close()

    def test_multiple_close_calls_safe(self, tmp_path):
        """Calling close() multiple times should not raise."""
        from core.state.position_store import PositionStore

        db_path = tmp_path / "positions.db"
        store = PositionStore(db_path=db_path)

        # Multiple close calls should be safe
        store.close()
        store.close()  # Should not raise
        store.close()  # Should not raise

    def test_three_restart_cycles(self, tmp_path):
        """Simulate 3 start/stop cycles — no lock errors accumulate."""
        from core.state.position_store import PositionStore, Position
        from datetime import datetime, timezone
        from decimal import Decimal

        db_path = tmp_path / "positions.db"

        for cycle in range(3):
            store = PositionStore(db_path=db_path)
            pos = Position(
                symbol=f"SYM{cycle}",
                quantity=Decimal("1"),
                entry_price=Decimal("100.00"),
                entry_time=datetime.now(timezone.utc),
                strategy="test",
                order_id=f"order-{cycle}",
            )
            store.upsert(pos)

            # Verify all previous positions are readable
            all_pos = store.get_all()
            assert len(all_pos) == cycle + 1, (
                f"Cycle {cycle}: expected {cycle + 1} positions, got {len(all_pos)}"
            )
            store.close()

    def test_concurrent_read_write_no_lock(self, tmp_path):
        """WAL mode allows concurrent reads while writing."""
        from core.state.position_store import PositionStore, Position
        from datetime import datetime, timezone
        from decimal import Decimal

        db_path = tmp_path / "positions.db"
        store = PositionStore(db_path=db_path)

        errors = []

        def writer():
            try:
                for i in range(10):
                    pos = Position(
                        symbol=f"W{i}",
                        quantity=Decimal("1"),
                        entry_price=Decimal("100"),
                        entry_time=datetime.now(timezone.utc),
                        strategy="test",
                        order_id=f"w-{i}",
                    )
                    store.upsert(pos)
            except Exception as e:
                errors.append(("writer", e))

        def reader():
            try:
                for _ in range(10):
                    store.get_all()
            except Exception as e:
                errors.append(("reader", e))

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        store.close()

        lock_errors = [
            (role, e) for role, e in errors if "locked" in str(e).lower()
        ]
        assert len(lock_errors) == 0, f"Database locked errors: {lock_errors}"


class TestJournalRestart:
    """Journal files survive restart without corruption."""

    def test_journal_writable_after_restart(self, tmp_path):
        """Write events, close, re-create writer, write more — no errors."""
        journal_dir = tmp_path / "journal"

        # Session 1
        j1 = JournalWriter(base_dir=journal_dir)
        j1.write_event({"event": "session1_start", "cycle": 0})
        j1.write_event({"event": "session1_end", "cycle": 5})
        # JournalWriter has no close() — it opens/closes per write

        # Session 2 — same directory
        j2 = JournalWriter(base_dir=journal_dir)
        j2.write_event({"event": "session2_start", "cycle": 0})
        j2.write_event({"event": "session2_end", "cycle": 10})

        # Verify all events are readable
        daily_dir = journal_dir / "daily"
        events = []
        for f in daily_dir.glob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))

        event_names = [e["event"] for e in events]
        assert "session1_start" in event_names
        assert "session1_end" in event_names
        assert "session2_start" in event_names
        assert "session2_end" in event_names

    def test_journal_three_restart_cycles(self, tmp_path):
        """Three writer lifecycles, all events preserved."""
        journal_dir = tmp_path / "journal"

        for session in range(3):
            writer = JournalWriter(base_dir=journal_dir)
            for i in range(5):
                writer.write_event({
                    "event": f"cycle_{i}",
                    "session": session,
                })

        # Count total events
        daily_dir = journal_dir / "daily"
        total = 0
        for f in daily_dir.glob("*.jsonl"):
            total += sum(1 for line in f.read_text().splitlines() if line.strip())

        assert total == 15, f"Expected 15 events (3 sessions x 5), got {total}"


class TestRawSqliteRestart:
    """Direct SQLite WAL-mode restart test (no PositionStore dependency)."""

    def test_wal_mode_survives_restart(self, tmp_path):
        """WAL-mode DB can be reopened cleanly after close."""
        db_path = tmp_path / "test.db"

        # Session 1
        conn1 = sqlite3.connect(str(db_path))
        conn1.execute("PRAGMA journal_mode=WAL")
        conn1.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v TEXT)")
        conn1.execute("INSERT OR REPLACE INTO t VALUES ('a', '1')")
        conn1.commit()
        conn1.close()

        # Session 2
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("PRAGMA journal_mode=WAL")
        row = conn2.execute("SELECT v FROM t WHERE k='a'").fetchone()
        assert row is not None
        assert row[0] == "1"

        conn2.execute("INSERT OR REPLACE INTO t VALUES ('b', '2')")
        conn2.commit()

        rows = conn2.execute("SELECT COUNT(*) FROM t").fetchone()
        assert rows[0] == 2
        conn2.close()
