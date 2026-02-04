"""
P1 Patch 6: Idempotent replay handler for TransactionLog events.

Deduplicates events by a composite key so that replaying the same
log twice (e.g. after a crash) never produces duplicate side effects.
"""

from __future__ import annotations

from typing import Callable, Dict, Set


class IdempotentReplayHandler:
    """
    Wraps a user-supplied callback and deduplicates events by composite key.

    Key = (event_type, internal_order_id) when internal_order_id is present,
    otherwise (event_type, _logged_at).

    Attributes:
        duplicates_skipped: count of events that were skipped as duplicates.
    """

    def __init__(self, callback: Callable[[Dict], None]) -> None:
        self._callback = callback
        self._seen: Set[tuple] = set()
        self.duplicates_skipped: int = 0

    def _make_key(self, event: Dict) -> tuple:
        event_type = event.get("event_type", "")
        order_id = event.get("internal_order_id")
        if order_id:
            return (event_type, order_id)
        return (event_type, event.get("_logged_at", ""))

    def handle(self, event: Dict) -> None:
        key = self._make_key(event)
        if key in self._seen:
            self.duplicates_skipped += 1
            return
        self._seen.add(key)
        self._callback(event)
