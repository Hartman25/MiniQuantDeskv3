"""
Consecutive-failure circuit breaker for the runtime loop.

INVARIANT:
    After MAX consecutive unhandled exceptions the breaker trips,
    signalling the loop to halt safely.

DESIGN:
    - record_failure() increments the counter.
    - record_success() resets it to zero.
    - is_tripped returns True when count >= max_failures.
    - Thread-safe (single runtime thread, but safe regardless).
"""

from __future__ import annotations

import threading


class ConsecutiveFailureBreaker:
    """Trips after *max_failures* consecutive failures without a success."""

    def __init__(self, max_failures: int = 5) -> None:
        self.max_failures: int = max_failures
        self._count: int = 0
        self._lock = threading.Lock()

    # -- public API ----------------------------------------------------------

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._count

    @property
    def is_tripped(self) -> bool:
        with self._lock:
            return self._count >= self.max_failures

    def record_failure(self) -> None:
        with self._lock:
            self._count += 1

    def record_success(self) -> None:
        with self._lock:
            self._count = 0
