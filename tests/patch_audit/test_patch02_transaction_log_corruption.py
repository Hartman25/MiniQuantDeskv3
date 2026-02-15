"""
PATCH 2 tests: Transaction log fsync + CRC32 validation.

Tests:
1. Corrupt line causes load failure with explicit error
2. Append ensures fsync (best-effort verification)
3. Backward compatibility with legacy format (no checksum)
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.state.transaction_log import (
    TransactionLog,
    TransactionLogCorruptionError,
)


def test_corrupt_line_causes_load_failure():
    """PATCH 2: Corrupt line (checksum mismatch) raises TransactionLogCorruptionError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "txlog.ndjson"

        # Write a valid line
        log = TransactionLog(log_path)
        log.append({
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "test-001",
            "symbol": "SPY",
        })
        log.close()

        # Corrupt the file by modifying the JSON data but keeping the checksum
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        checksum, _, json_data = lines[0].partition(":")

        # Tamper with JSON data
        tampered_json = json_data.replace("SPY", "XXX")
        corrupted_line = f"{checksum}:{tampered_json}"
        log_path.write_text(corrupted_line + "\n", encoding="utf-8")

        # Attempt to read should fail
        log2 = TransactionLog(log_path)
        with pytest.raises(TransactionLogCorruptionError) as exc_info:
            list(log2.iter_events())

        assert "corruption detected" in str(exc_info.value).lower()
        assert "checksum mismatch" in str(exc_info.value).lower()
        log2.close()


def test_append_includes_checksum():
    """PATCH 2: Appended lines include CRC32 checksum prefix."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "txlog.ndjson"

        log = TransactionLog(log_path)
        log.append({
            "event_type": "ORDER_FILLED",
            "internal_order_id": "test-002",
            "symbol": "AAPL",
        })
        log.close()

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        # Format: "checksum:json_data"
        assert ":" in lines[0]
        checksum_str, _, json_str = lines[0].partition(":")
        assert len(checksum_str) == 8  # 8-char hex
        assert all(c in "0123456789abcdef" for c in checksum_str.lower())

        # Verify JSON is valid
        data = json.loads(json_str)
        assert data["event_type"] == "ORDER_FILLED"
        assert data["symbol"] == "AAPL"


def test_backward_compatibility_with_legacy_format():
    """PATCH 2: Legacy lines without checksums are still readable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "txlog.ndjson"

        # Write legacy format (no checksum)
        legacy_event = {
            "event_type": "ORDER_SUBMIT",
            "internal_order_id": "legacy-001",
            "symbol": "TSLA",
        }
        legacy_line = json.dumps(legacy_event)
        log_path.write_text(legacy_line + "\n", encoding="utf-8")

        # Should read without error
        log = TransactionLog(log_path)
        events = list(log.iter_events())
        log.close()

        assert len(events) == 1
        assert events[0]["symbol"] == "TSLA"
        assert events[0]["internal_order_id"] == "legacy-001"


def test_mixed_legacy_and_checksummed_lines():
    """PATCH 2: Can read log with both legacy and checksummed lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "txlog.ndjson"

        # Write legacy line
        legacy_event = {"event_type": "ORDER_SUBMIT", "internal_order_id": "leg-1", "symbol": "A"}
        legacy_line = json.dumps(legacy_event)
        log_path.write_text(legacy_line + "\n", encoding="utf-8")

        # Append new checksummed line
        log = TransactionLog(log_path)
        log.append({"event_type": "ORDER_FILLED", "internal_order_id": "new-1", "symbol": "B"})
        log.close()

        # Read both
        log2 = TransactionLog(log_path)
        events = list(log2.iter_events())
        log2.close()

        assert len(events) == 2
        assert events[0]["symbol"] == "A"  # legacy
        assert events[1]["symbol"] == "B"  # checksummed


def test_fsync_called_on_append():
    """PATCH 2: Verify fsync is attempted (best-effort on Windows)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "txlog.ndjson"

        log = TransactionLog(log_path)

        # Mock fsync to verify it's called
        import os
        original_fsync = os.fsync
        fsync_called = []

        def mock_fsync(fd):
            fsync_called.append(fd)
            # Call original to ensure actual sync (if supported)
            try:
                original_fsync(fd)
            except (OSError, AttributeError):
                pass

        os.fsync = mock_fsync
        try:
            log.append({
                "event_type": "ORDER_SUBMIT",
                "internal_order_id": "sync-001",
                "symbol": "SPY",
            })

            # fsync should have been called (or attempted)
            assert len(fsync_called) >= 1, "fsync was not called after append"
        finally:
            os.fsync = original_fsync
            log.close()
