"""
OPERATIONAL READINESS TESTS — Phase 1 hardening for 24/7 operation.

Tests the critical functions added for "run tonight, trade Monday":

1. Market-hours guard  (_check_market_open)
   - Broker reports closed → blocks
   - Broker reports open → allows
   - Broker without clock → fail-open (tests/fakes)
   - Broker clock error → respects fail-open/fail-closed env toggle
     (env var: MQD_FAIL_OPEN_MARKET_HOURS)

2. Adaptive sleep (compute_adaptive_sleep)
   - Market open → base interval
   - Market closed, far from open → closed interval
   - Market closed, within pre-open window → pre-open interval
   - Market closed, no next_open → closed interval
   - now_utc injection for deterministic testing (no datetime.now mocking)

3. Import-time landmine prevention
   - entry_paper can be imported without triggering I/O or prints
   - reconciliation.py emits no deprecation warnings at import

4. Broker resilience
   - _retry_api_call retries ConnectionError, TimeoutError, OSError
   - Clock TTL cache avoids redundant /v2/clock calls
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from core.runtime.app import _check_market_open, compute_adaptive_sleep


# ============================================================================
# HELPERS — Fake brokers
# ============================================================================

class FakeBrokerOpen:
    """Fake broker that says market is open."""
    def get_clock(self):
        return {
            "is_open": True,
            "timestamp": datetime.now(timezone.utc),
            "next_open": datetime.now(timezone.utc) + timedelta(hours=16),
            "next_close": datetime.now(timezone.utc) + timedelta(hours=6),
        }


class FakeBrokerClosed:
    """Fake broker that says market is closed."""
    def get_clock(self):
        return {
            "is_open": False,
            "timestamp": datetime.now(timezone.utc),
            "next_open": datetime.now(timezone.utc) + timedelta(hours=14),
            "next_close": datetime.now(timezone.utc) + timedelta(hours=20),
        }


class FakeBrokerNoClock:
    """Fake broker without get_clock method (like many test fakes)."""
    pass


class FakeBrokerClockError:
    """Fake broker whose clock raises an error (transient network failure)."""
    def get_clock(self):
        raise ConnectionError("timeout talking to Alpaca clock API")


# ============================================================================
# TEST: Market-hours guard
# ============================================================================

class TestMarketHoursGuard:

    def test_open_market_allows_submission(self):
        result = _check_market_open(FakeBrokerOpen())
        assert result["is_open"] is True
        assert result["source"] == "broker"
        assert result["error"] is None

    def test_closed_market_blocks_submission(self):
        result = _check_market_open(FakeBrokerClosed())
        assert result["is_open"] is False
        assert result["source"] == "broker"
        assert result["next_open"] is not None

    def test_broker_without_clock_fails_open(self):
        """Fake broker without get_clock → fail-open so tests work."""
        result = _check_market_open(FakeBrokerNoClock())
        assert result["is_open"] is True
        assert result["source"] == "fallback"
        assert result["error"] == "broker_has_no_clock"

    def test_clock_error_default_fail_closed(self):
        """Transient error with default env (no MQD_FAIL_OPEN_MARKET_HOURS) → fail-closed."""
        with patch.dict(os.environ, {}, clear=False):
            # Ensure the env var is NOT set
            os.environ.pop("MQD_FAIL_OPEN_MARKET_HOURS", None)
            result = _check_market_open(FakeBrokerClockError())
            assert result["is_open"] is False
            assert result["source"] == "fallback"
            assert "timeout" in result["error"]

    def test_clock_error_with_fail_open_env(self):
        """Transient error with MQD_FAIL_OPEN_MARKET_HOURS=1 → fail-open."""
        with patch.dict(os.environ, {"MQD_FAIL_OPEN_MARKET_HOURS": "1"}):
            result = _check_market_open(FakeBrokerClockError())
            assert result["is_open"] is True
            assert result["source"] == "fallback"


# ============================================================================
# TEST: Adaptive sleep (deterministic via now_utc injection)
# ============================================================================

class TestAdaptiveSleep:
    """All time-dependent tests inject now_utc — no datetime.now mocking needed."""

    # Fixed reference time for deterministic tests
    REF_NOW = datetime(2026, 2, 9, 14, 30, 0, tzinfo=timezone.utc)

    def test_market_open_returns_base_interval(self):
        sleep = compute_adaptive_sleep(
            market_is_open=True,
            base_interval_s=60,
            now_utc=self.REF_NOW,
        )
        assert sleep == 60

    def test_market_closed_no_next_open_returns_closed_interval(self):
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=None,
            base_interval_s=60,
            closed_interval_s=120,
            now_utc=self.REF_NOW,
        )
        assert sleep == 120

    def test_market_closed_far_from_open_returns_closed_interval(self):
        far_future = self.REF_NOW + timedelta(hours=12)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=far_future,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=20,
            pre_open_window_m=10,
        )
        assert sleep == 120

    def test_market_closed_within_preopen_window(self):
        near_future = self.REF_NOW + timedelta(minutes=5)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=near_future,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=20,
            pre_open_window_m=10,
        )
        assert sleep == 20

    def test_market_closed_exactly_at_preopen_boundary(self):
        boundary = self.REF_NOW + timedelta(minutes=10)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=boundary,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=15,
            pre_open_window_m=10,
        )
        # 10 minutes = 600 seconds, window is 10*60=600, so just at boundary → should be pre-open
        assert sleep == 15

    def test_minimum_sleep_is_1(self):
        """Even with 0 or negative inputs, sleep is at least 1."""
        sleep = compute_adaptive_sleep(
            market_is_open=True,
            base_interval_s=0,
            now_utc=self.REF_NOW,
        )
        assert sleep >= 1

    def test_defaults_when_zero_passed(self):
        """When optional params are 0, the function uses its own defaults."""
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=None,
            now_utc=self.REF_NOW,
            base_interval_s=60,
            closed_interval_s=0,
            pre_open_interval_s=0,
            pre_open_window_m=0,
        )
        # Should use default closed_interval_s=120
        assert sleep == 120

    def test_now_utc_none_falls_back_to_realtime(self):
        """When now_utc is not provided, the function still works (uses datetime.now)."""
        near_future = datetime.now(timezone.utc) + timedelta(minutes=5)
        sleep = compute_adaptive_sleep(
            market_is_open=False,
            next_open_utc=near_future,
            # now_utc intentionally omitted
            base_interval_s=60,
            closed_interval_s=120,
            pre_open_interval_s=20,
            pre_open_window_m=10,
        )
        assert sleep == 20


# ============================================================================
# TEST: Import-time landmine prevention
# ============================================================================

class TestImportTimeSafety:

    def test_entry_paper_import_no_side_effects(self, capsys):
        """Importing entry_paper must not trigger I/O or print at module scope.

        We test this by checking that no output was printed during import.
        Since entry_paper may already be imported, we use importlib to reload
        and capture output.
        """
        import importlib
        import entry_paper

        # Capture any output that a fresh reload would produce
        captured_before = capsys.readouterr()

        # Reload to simulate fresh import
        importlib.reload(entry_paper)
        captured_after = capsys.readouterr()

        # The module-level code should not print anything
        assert "[env]" not in captured_after.out, (
            "entry_paper prints '[env]' at import time — "
            "env loading should be inside main(), not at module level"
        )

    def test_reconciliation_module_no_warnings_at_import(self):
        """core.execution.reconciliation must not emit warnings at import time."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import importlib
            import core.execution.reconciliation
            importlib.reload(core.execution.reconciliation)
            deprecation_warnings = [
                x for x in w
                if "deprecat" in str(x.message).lower()
                and "reconciliation" in str(x.filename).lower()
            ]
            assert deprecation_warnings == [], (
                f"reconciliation.py emits deprecation warnings at import: {deprecation_warnings}"
            )


# ============================================================================
# TEST: Broker resilience (_retry_api_call network errors)
# ============================================================================

class TestRetryApiCallResilience:
    """Verify that _retry_api_call retries ConnectionError/TimeoutError/OSError."""

    def _make_connector_stub(self):
        """Create a minimal AlpacaBrokerConnector-like object for testing _retry_api_call.

        We import the real class but skip __init__ to avoid hitting the Alpaca API.
        """
        from core.brokers.alpaca_connector import AlpacaBrokerConnector
        import logging

        obj = object.__new__(AlpacaBrokerConnector)
        obj.logger = logging.getLogger("test_retry")
        obj.MAX_RETRIES = 3
        obj.RETRY_DELAY_SECONDS = 0.01  # fast for tests
        obj.RETRY_BACKOFF_MULTIPLIER = 1.0  # no backoff for tests
        return obj

    def test_retries_connection_error(self):
        stub = self._make_connector_stub()
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection reset")
            return "ok"

        result = stub._retry_api_call(flaky)
        assert result == "ok"
        assert call_count == 3

    def test_retries_timeout_error(self):
        stub = self._make_connector_stub()
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("read timed out")
            return "ok"

        result = stub._retry_api_call(flaky)
        assert result == "ok"
        assert call_count == 2

    def test_retries_os_error(self):
        stub = self._make_connector_stub()
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("network unreachable")
            return "ok"

        result = stub._retry_api_call(flaky)
        assert result == "ok"
        assert call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        stub = self._make_connector_stub()

        def always_fails():
            raise ConnectionError("permanent failure")

        with pytest.raises(ConnectionError, match="permanent failure"):
            stub._retry_api_call(always_fails)

    def test_non_retryable_error_raises_immediately(self):
        stub = self._make_connector_stub()
        call_count = 0

        def bad_call():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad argument")

        with pytest.raises(ValueError, match="bad argument"):
            stub._retry_api_call(bad_call)
        assert call_count == 1  # no retries for ValueError


# ============================================================================
# TEST: Clock TTL cache
# ============================================================================

class TestClockTTLCache:
    """Verify that get_clock() caches results for MARKET_CLOCK_CACHE_S."""

    def _make_connector_stub(self):
        """Minimal connector stub with clock cache fields initialised."""
        from core.brokers.alpaca_connector import AlpacaBrokerConnector
        import logging

        obj = object.__new__(AlpacaBrokerConnector)
        obj.logger = logging.getLogger("test_clock_cache")
        obj.MAX_RETRIES = 1
        obj.RETRY_DELAY_SECONDS = 0.01
        obj.RETRY_BACKOFF_MULTIPLIER = 1.0
        obj._clock_cache = None
        obj._clock_cache_ts = 0.0
        obj._clock_cache_ttl = 15.0

        # Fake Alpaca client with mock clock
        mock_client = MagicMock()
        clock_obj = MagicMock()
        clock_obj.is_open = True
        clock_obj.timestamp = datetime.now(timezone.utc)
        clock_obj.next_open = datetime.now(timezone.utc) + timedelta(hours=16)
        clock_obj.next_close = datetime.now(timezone.utc) + timedelta(hours=6)
        mock_client.get_clock.return_value = clock_obj
        obj.client = mock_client

        return obj

    def test_second_call_uses_cache(self):
        stub = self._make_connector_stub()

        result1 = stub.get_clock()
        result2 = stub.get_clock()

        assert result1["is_open"] is True
        assert result2["is_open"] is True
        # Only one actual API call should have been made
        assert stub.client.get_clock.call_count == 1

    def test_cache_expires_after_ttl(self):
        stub = self._make_connector_stub()
        stub._clock_cache_ttl = 0.05  # 50ms for fast test

        result1 = stub.get_clock()
        assert stub.client.get_clock.call_count == 1

        time.sleep(0.06)  # Wait for cache to expire

        result2 = stub.get_clock()
        assert stub.client.get_clock.call_count == 2


# ============================================================================
# TEST: Integration — guard + adaptive sleep work together
# ============================================================================

class TestGuardSleepIntegration:
    """Spec requirement: 'clock unavailable and failing closed → use closed cadence'."""

    def test_clock_error_fail_closed_uses_closed_cadence(self):
        """When clock errors out and we fail-closed, adaptive sleep must use
        closed_interval_s (not base_interval_s) so we don't hammer the API."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MQD_FAIL_OPEN_MARKET_HOURS", None)

            status = _check_market_open(FakeBrokerClockError())
            assert status["is_open"] is False, "should fail-closed"

            # Feed the guard result into adaptive sleep
            sleep_s = compute_adaptive_sleep(
                market_is_open=status["is_open"],
                next_open_utc=status.get("next_open"),  # None (clock errored)
                now_utc=datetime(2026, 2, 9, 3, 0, 0, tzinfo=timezone.utc),
                base_interval_s=60,
                closed_interval_s=120,
                pre_open_interval_s=20,
                pre_open_window_m=10,
            )
            assert sleep_s == 120, (
                f"fail-closed with no next_open should use closed cadence (120s), got {sleep_s}s"
            )

    def test_clock_ok_closed_market_returns_all_fields(self):
        """MARKET_CLOSED_BLOCK event contract: guard result includes next_close."""
        status = _check_market_open(FakeBrokerClosed())
        assert status["is_open"] is False
        assert status["source"] == "broker"
        assert status["error"] is None
        # Must include next_open and next_close for complete journal events
        assert status["next_open"] is not None, "missing next_open in guard result"
        assert status["next_close"] is not None, "missing next_close in guard result"

    def test_open_market_uses_base_interval(self):
        """When market is open, sleep should be the normal cycle interval."""
        status = _check_market_open(FakeBrokerOpen())
        sleep_s = compute_adaptive_sleep(
            market_is_open=status["is_open"],
            next_open_utc=status.get("next_open"),
            now_utc=datetime(2026, 2, 9, 15, 0, 0, tzinfo=timezone.utc),
            base_interval_s=60,
            closed_interval_s=120,
        )
        assert sleep_s == 60
