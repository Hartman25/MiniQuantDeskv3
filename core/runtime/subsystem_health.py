"""
P1 Patch 5: Subsystem health monitor for safe degradation.

Tracks per-subsystem consecutive failures and signals when a
critical subsystem exceeds its threshold, triggering a trading halt.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Set


class SubsystemHealthMonitor:
    """
    Tracks health of named subsystems.

    - `record_ok(name)` resets the failure counter for *name*.
    - `record_failure(name)` increments it.
    - `should_halt()` returns True if ANY critical subsystem has
      consecutive failures >= threshold.
    """

    def __init__(
        self,
        critical_subsystems: Optional[Set[str]] = None,
        failure_threshold: int = 5,
    ) -> None:
        self._critical: Set[str] = critical_subsystems or set()
        self._threshold: int = failure_threshold
        self._failures: Dict[str, int] = {}
        self._lock = threading.Lock()

    def record_ok(self, name: str) -> None:
        with self._lock:
            self._failures[name] = 0

    def record_failure(self, name: str) -> None:
        with self._lock:
            self._failures[name] = self._failures.get(name, 0) + 1

    def should_halt(self) -> bool:
        with self._lock:
            for name in self._critical:
                if self._failures.get(name, 0) >= self._threshold:
                    return True
            return False

    def get_status(self) -> Dict[str, Dict]:
        with self._lock:
            return {
                name: {"consecutive_failures": count}
                for name, count in self._failures.items()
            }
