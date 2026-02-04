"""
P1 Patch 6 – Event Replay Idempotency

INVARIANT:
    Replaying TransactionLog events through a handler must not produce
    duplicate side effects.  The replay handler must be idempotent:
    processing the same event twice yields the same result as once.

WHY THIS MATTERS:
    TransactionLog.replay(handler) exists but has no deduplication.
    If recovery replays events, a naive handler would re-submit orders,
    double-count fills, or corrupt position state.

DESIGN:
    - New class `IdempotentReplayHandler` that deduplicates events by
      a composite key (event_type + internal_order_id).
    - Calls a user-supplied callback only for first-seen events.
    - Returns count of duplicates skipped.
"""

import pytest
from datetime import datetime, timezone


class TestIdempotentReplayHandler:
    """Unit tests for replay deduplication."""

    def test_first_event_is_processed(self):
        from core.runtime.replay_handler import IdempotentReplayHandler

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))

        handler.handle({
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "ORD-001",
            "_logged_at": "2026-01-30T10:00:00Z",
        })

        assert len(processed) == 1

    def test_duplicate_event_is_skipped(self):
        from core.runtime.replay_handler import IdempotentReplayHandler

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))

        event = {
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "ORD-001",
            "_logged_at": "2026-01-30T10:00:00Z",
        }
        handler.handle(event)
        handler.handle(event)  # duplicate

        assert len(processed) == 1
        assert handler.duplicates_skipped == 1

    def test_different_events_are_both_processed(self):
        from core.runtime.replay_handler import IdempotentReplayHandler

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))

        handler.handle({
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "ORD-001",
            "_logged_at": "2026-01-30T10:00:00Z",
        })
        handler.handle({
            "event_type": "FILL",
            "internal_order_id": "ORD-001",
            "_logged_at": "2026-01-30T10:00:01Z",
        })

        assert len(processed) == 2
        assert handler.duplicates_skipped == 0

    def test_same_order_different_event_type_not_duplicate(self):
        from core.runtime.replay_handler import IdempotentReplayHandler

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))

        handler.handle({
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "ORD-001",
        })
        handler.handle({
            "event_type": "CANCEL",
            "internal_order_id": "ORD-001",
        })

        assert len(processed) == 2

    def test_events_without_order_id_use_logged_at(self):
        from core.runtime.replay_handler import IdempotentReplayHandler

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))

        handler.handle({
            "event_type": "BOOT",
            "_logged_at": "2026-01-30T10:00:00Z",
        })
        handler.handle({
            "event_type": "BOOT",
            "_logged_at": "2026-01-30T10:00:00Z",
        })

        assert len(processed) == 1

    def test_integrates_with_transaction_log_replay(self, tmp_path):
        """End-to-end: write events, replay through handler, verify no dups."""
        from core.state.transaction_log import TransactionLog
        from core.runtime.replay_handler import IdempotentReplayHandler

        log_path = tmp_path / "txn.ndjson"
        txlog = TransactionLog(path=log_path)

        # Write same logical event twice (simulating crash before
        # the first was marked processed)
        for _ in range(2):
            txlog.append({
                "event_type": "ORDER_SUBMIT",
                "internal_order_id": "ORD-DUP",
            })
        txlog.append({
            "event_type": "FILL",
            "internal_order_id": "ORD-DUP",
        })
        txlog.close()

        txlog2 = TransactionLog(path=log_path)

        processed = []
        handler = IdempotentReplayHandler(callback=lambda ev: processed.append(ev))
        txlog2.replay(handler.handle)

        # Should see 2 unique events (one ORDER_SUBMIT + one FILL),
        # not 3 (the duplicate submit is deduped)
        assert len(processed) == 2
        assert handler.duplicates_skipped == 1
        txlog2.close()
