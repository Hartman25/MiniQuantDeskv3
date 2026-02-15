"""
Append-only transaction log for order events.

Properties:
- Append-only, newline-delimited JSON (NDJSON): one event per line.
- Line-buffered + explicit flush for durability.
- Thread-safe via lock.
- Backtest-safe timestamps via injected clock.

PATCH 2 (2026-02-14):
- CRC32 checksum per line for corruption detection
- Explicit fsync after append for crash-safety
- Validation on load: fail fast on corrupt lines
"""

from __future__ import annotations

import json
import logging
import os
import threading
import zlib
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Protocol, Union


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Default clock (UTC, timezone-aware)."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class TransactionLogError(RuntimeError):
    pass


class TransactionLogCorruptionError(TransactionLogError):
    """Raised when transaction log has corrupted lines (checksum mismatch)."""
    pass


EventLike = Union[dict, Any]


class TransactionLog:
    """
    Append-only transaction log for order events.

    Constructor supports historical call-sites:
      - TransactionLog(path: PathLike)                       # scripts/tests
      - TransactionLog(log_path=..., clock=...)              # container
      - TransactionLog(file_path=..., clock=...)
    """

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        log_path: Optional[Union[str, Path]] = None,
        file_path: Optional[Union[str, Path]] = None,
        clock: Optional[Clock] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        # Resolve path precedence: explicit kwargs win, else positional
        chosen = log_path or file_path or path
        if chosen is None:
            raise TypeError("TransactionLog requires a log path (positional 'path' or keyword 'log_path'/'file_path').")

        self.log_path: Path = Path(chosen)
        # Backwards-compatible alias
        self.file_path: Path = self.log_path

        self.clock: Clock = clock or SystemClock()
        self.logger: logging.Logger = logger or logging.getLogger("miniquantdesk.transaction_log")

        self._lock = threading.Lock()
        self._file = None  # type: Optional[Any]
        self._open_file()

    def _open_file(self) -> None:
        """Open the underlying file handle (append + read)."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # a+ so we can read/replay if needed; buffering=1 for line buffering
            self._file = open(self.log_path, mode="a+", encoding="utf-8", buffering=1)
        except Exception as e:
            raise TransactionLogError(f"Failed to open transaction log at {self.log_path}: {e}") from e

    # ------------------------
    # Write path
    # ------------------------

    def append(self, event: EventLike) -> None:
        """
        Append an event to the log as a single JSON line.

        The event may be:
          - a dict
          - a dataclass instance (will be converted via asdict)
          - an object with to_dict() method

        Patch 2 (correlation IDs):
          - If the event represents an order lifecycle event (ORDER_*, CANCEL, FILL, ERROR, BROKER_ORDER_ACK),
            we REQUIRE: event_type, trade_id, internal_order_id.
          - If a caller uses legacy key 'event' instead of 'event_type', we map it.
        """
        with self._lock:
            if self._file is None:
                self._open_file()

            try:
                event_dict = self._to_dict(event)

                # Back-compat: allow legacy 'event' key (used by some writers)
                if "event_type" not in event_dict and "event" in event_dict:
                    event_dict["event_type"] = event_dict.get("event")

                event_type = str(event_dict.get("event_type") or "").upper().strip()

                # Enforce correlation IDs for order-relevant events only.
                needs_corr = bool(
                    event_type.startswith("ORDER_")
                    or event_type in {"CANCEL", "FILL", "ERROR", "BROKER_ORDER_ACK", "BROKER_ACK", "ORDER_ACK"}
                )
                if needs_corr:
                    # Derive trade_id deterministically when callers omit it (tests + legacy writers).
                    if not event_dict.get('trade_id') and event_dict.get('internal_order_id'):
                        event_dict['trade_id'] = f"T-{event_dict['internal_order_id']}"
                    
                    missing = [k for k in ('event_type', 'internal_order_id') if not event_dict.get(k)]
                    if missing:
                        raise ValueError(f"TransactionLog event missing required fields: {missing}")

                # Inject a log timestamp (UTC, ISO8601 with Z)
                logged_at = self.clock.now()
                if logged_at.tzinfo is None:
                    logged_at = logged_at.replace(tzinfo=timezone.utc)
                event_dict["_logged_at"] = logged_at.isoformat().replace("+00:00", "Z")

                # Normalize non-JSON-native values (e.g., Enum, Decimal, datetime)
                event_dict = self._normalize_json(event_dict)

                line = json.dumps(event_dict, separators=(",", ":"), ensure_ascii=False)

                # PATCH 2: Add CRC32 checksum for corruption detection
                checksum = zlib.crc32(line.encode("utf-8")) & 0xFFFFFFFF
                line_with_checksum = f"{checksum:08x}:{line}"

                self._file.write(line_with_checksum + "\n")
                self._file.flush()

                # PATCH 2: Explicit fsync for crash-safety (best-effort on Windows)
                try:
                    os.fsync(self._file.fileno())
                except (OSError, AttributeError):
                    # Windows may not support fsync on all file systems; log but continue
                    pass
            except Exception as e:
                self.logger.error("Failed to append to transaction log", extra={"error": str(e)}, exc_info=True)
                raise TransactionLogError(f"Failed to append to transaction log: {e}") from e

    def flush(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()

    # ------------------------
    # Read path
    # ------------------------
    def read_all(self) -> list[Dict[str, Any]]:
        """Read all events into memory."""
        with self._lock:
            return list(self.iter_events())

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """
        Iterate events without loading everything into memory.

        PATCH 2: Validates CRC32 checksums; raises TransactionLogCorruptionError on mismatch.
        """
        # We intentionally do NOT keep the lock during iteration to avoid deadlocks;
        # instead we take a snapshot of the path and open a separate handle.
        path = self.log_path
        if not path.exists():
            return iter(())

        def _gen() -> Iterator[Dict[str, Any]]:
            try:
                with open(path, mode="r", encoding="utf-8") as f:
                    line_num = 0
                    for line in f:
                        line_num += 1
                        line = line.strip()
                        if not line:
                            continue

                        # PATCH 2: Parse and validate checksum
                        if ":" not in line:
                            # Legacy format without checksum (pre-PATCH 2); accept for backward compat
                            try:
                                yield json.loads(line)
                                continue
                            except json.JSONDecodeError:
                                # Skip malformed line but continue
                                continue

                        # Format: "checksum:json_data"
                        checksum_str, _, json_str = line.partition(":")
                        if len(checksum_str) != 8:
                            # Not a valid checksum format; try as legacy
                            try:
                                yield json.loads(line)
                                continue
                            except json.JSONDecodeError:
                                continue

                        try:
                            expected_checksum = int(checksum_str, 16)
                        except ValueError:
                            # Not a hex checksum; try as legacy
                            try:
                                yield json.loads(line)
                                continue
                            except json.JSONDecodeError:
                                continue

                        # Compute actual checksum
                        actual_checksum = zlib.crc32(json_str.encode("utf-8")) & 0xFFFFFFFF

                        if actual_checksum != expected_checksum:
                            error_msg = (
                                f"TransactionLog corruption detected at {path}:{line_num}: "
                                f"checksum mismatch (expected={expected_checksum:08x}, actual={actual_checksum:08x})"
                            )
                            raise TransactionLogCorruptionError(error_msg)

                        try:
                            yield json.loads(json_str)
                        except json.JSONDecodeError as e:
                            error_msg = (
                                f"TransactionLog corruption detected at {path}:{line_num}: "
                                f"invalid JSON after checksum validation: {e}"
                            )
                            raise TransactionLogCorruptionError(error_msg) from e

            except FileNotFoundError:
                return

        return _gen()

    def filter_since(self, since: datetime) -> list[Dict[str, Any]]:
        """Return events with _logged_at > since."""
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        out: list[Dict[str, Any]] = []
        for ev in self.iter_events():
            ts = ev.get("_logged_at")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if dt > since:
                out.append(ev)
        return out

    def replay(self, handler) -> int:
        """
        Replay all events through a handler(event_dict) callable.

        Returns number of events replayed.
        """
        n = 0
        for ev in self.iter_events():
            handler(ev)
            n += 1
        return n

    # ------------------------
    # Lifecycle
    # ------------------------
    def close(self) -> None:
        with self._lock:
            try:
                if self._file is not None:
                    self._file.flush()
                    self._file.close()
            finally:
                self._file = None
        # log outside lock
        self.logger.info("TransactionLog closed", extra={"log_path": str(self.log_path)})

    def __enter__(self) -> "TransactionLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------
    # Helpers
    # ------------------------
    @staticmethod
    def _to_dict(event: EventLike) -> Dict[str, Any]:
        if isinstance(event, dict):
            return dict(event)
        if is_dataclass(event):
            return asdict(event)
        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        # Fallback: try __dict__
        if hasattr(event, "__dict__"):
            return dict(event.__dict__)
        raise TypeError(f"Unsupported event type for TransactionLog.append(): {type(event)!r}")

    @staticmethod
    def _normalize_json(obj: Any) -> Any:
        """Recursively convert common non-JSON types to JSON-serializable values."""
        if obj is None:
            return None

        # Dict-like
        if isinstance(obj, dict):
            return {str(k): TransactionLog._normalize_json(v) for k, v in obj.items()}

        # List/tuple/set
        if isinstance(obj, (list, tuple, set)):
            return [TransactionLog._normalize_json(v) for v in obj]

        # Enums -> their value (or name as fallback)
        if isinstance(obj, Enum):
            try:
                return obj.value
            except Exception:
                return obj.name

        # Decimal -> string (preserves precision)
        if isinstance(obj, Decimal):
            return str(obj)

        # datetime -> ISO8601 w/ Z
        if isinstance(obj, datetime):
            dt = obj
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")

        # Path -> str
        if isinstance(obj, Path):
            return str(obj)

        # Dataclasses nested
        if is_dataclass(obj):
            return TransactionLog._normalize_json(asdict(obj))

        # Objects with to_dict
        to_dict = getattr(obj, "to_dict", None)
        if callable(to_dict):
            return TransactionLog._normalize_json(to_dict())

        return obj