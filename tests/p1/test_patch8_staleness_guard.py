"""
PATCH 8 — Fail-closed on data staleness with explicit journal record

INVARIANT:
    Every data freshness decision (pass or reject) produces a journal-ready
    event dict.  Stale, incomplete, or missing bars are REJECTED and the
    rejection is auditable.

TESTS:
    1. Fresh, complete bar → PASSED verdict.
    2. Stale bar (age > threshold) → REJECTED, reason=stale.
    3. Incomplete bar → REJECTED, reason=incomplete.
    4. No bar (None) → REJECTED, reason=no_data.
    5. Custom threshold respected.
    6. Journal event always has required keys.
    7. Passed event has bar_age_s.
    8. Rejected-stale event carries bar_timestamp.
    9. require_complete=False skips completion check.
   10. Symbol fallback used when bar is None.
   11. StalenessVerdict is frozen (immutable).
   12. Completion check error → fail-closed rejection.
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from core.data.contract import MarketDataContract
from core.data.validator import StalenessGuard, StalenessVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(symbol="SPY", age_seconds=30, reference_time=None):
    """Create a MarketDataContract with a given age."""
    now = reference_time or datetime.now(timezone.utc)
    ts = now - timedelta(seconds=age_seconds)
    return MarketDataContract(
        symbol=symbol,
        timestamp=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.50"),
        volume=1000,
        provider="test",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStalenessGuard:

    def test_fresh_complete_bar_passes(self):
        """A fresh bar with age < threshold passes."""
        now = datetime.now(timezone.utc)
        # Bar 90 seconds old, threshold 65s but bar IS complete for 1Min
        # (1Min bar: complete when now > bar_ts + 60s + 5s grace = 65s)
        bar = _bar(age_seconds=70, reference_time=now)
        guard = StalenessGuard(max_staleness_s=120)

        verdict = guard.check(bar, timeframe="1Min", reference_time=now)
        assert verdict.ok is True
        assert verdict.reason is None
        assert verdict.event["outcome"] == "PASSED"

    def test_stale_bar_rejected(self):
        """Bar older than threshold is rejected with reason=stale."""
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=200, reference_time=now)
        guard = StalenessGuard(max_staleness_s=65)

        verdict = guard.check(bar, reference_time=now)
        assert verdict.ok is False
        assert verdict.reason == "stale"
        assert verdict.event["outcome"] == "REJECTED"
        assert verdict.event["reason"] == "stale"

    def test_incomplete_bar_rejected(self):
        """Bar that hasn't closed yet is rejected with reason=incomplete."""
        now = datetime.now(timezone.utc)
        # Bar only 10 seconds old → fresh but incomplete for 1Min (needs 65s)
        bar = _bar(age_seconds=10, reference_time=now)
        guard = StalenessGuard(max_staleness_s=120)

        verdict = guard.check(bar, timeframe="1Min", reference_time=now)
        assert verdict.ok is False
        assert verdict.reason == "incomplete"
        assert verdict.event["outcome"] == "REJECTED"
        assert verdict.event["reason"] == "incomplete"

    def test_no_bar_rejected(self):
        """None bar is rejected with reason=no_data."""
        guard = StalenessGuard()

        verdict = guard.check(None, symbol="AAPL")
        assert verdict.ok is False
        assert verdict.reason == "no_data"
        assert verdict.event["outcome"] == "REJECTED"
        assert verdict.event["reason"] == "no_data"
        assert verdict.event["symbol"] == "AAPL"

    def test_custom_threshold_respected(self):
        """A bar just within a larger threshold passes."""
        now = datetime.now(timezone.utc)
        # Bar 100s old, threshold 200s → should pass (if complete)
        bar = _bar(age_seconds=100, reference_time=now)
        guard = StalenessGuard(max_staleness_s=200, require_complete=False)

        verdict = guard.check(bar, reference_time=now)
        assert verdict.ok is True

    def test_event_has_required_keys(self):
        """Every journal event has the minimum required keys."""
        required = {"event", "symbol", "outcome", "timestamp"}
        guard = StalenessGuard()

        # Test with bar
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=70, reference_time=now)
        v1 = guard.check(bar, reference_time=now)
        assert required.issubset(v1.event.keys())

        # Test without bar
        v2 = guard.check(None, symbol="X")
        assert required.issubset(v2.event.keys())

    def test_passed_event_has_bar_age(self):
        """Passed verdict includes bar_age_s."""
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=70, reference_time=now)
        guard = StalenessGuard(max_staleness_s=120, require_complete=False)

        verdict = guard.check(bar, reference_time=now)
        assert verdict.ok is True
        assert "bar_age_s" in verdict.event
        assert isinstance(verdict.event["bar_age_s"], float)
        assert verdict.event["bar_age_s"] > 0

    def test_rejected_stale_has_bar_timestamp(self):
        """Rejected-stale verdict includes bar_timestamp."""
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=200, reference_time=now)
        guard = StalenessGuard(max_staleness_s=65)

        verdict = guard.check(bar, reference_time=now)
        assert verdict.ok is False
        assert "bar_timestamp" in verdict.event
        assert verdict.event["bar_timestamp"] == bar.timestamp.isoformat()

    def test_require_complete_false_skips_check(self):
        """With require_complete=False, an incomplete but fresh bar passes."""
        now = datetime.now(timezone.utc)
        # Bar 10s old → fresh but not yet complete for 1Min
        bar = _bar(age_seconds=10, reference_time=now)
        guard = StalenessGuard(max_staleness_s=120, require_complete=False)

        verdict = guard.check(bar, timeframe="1Min", reference_time=now)
        assert verdict.ok is True

    def test_symbol_fallback_when_bar_none(self):
        """When bar is None, symbol param is used in the event."""
        guard = StalenessGuard()
        verdict = guard.check(None, symbol="TSLA")
        assert verdict.event["symbol"] == "TSLA"

    def test_verdict_is_frozen(self):
        """StalenessVerdict is immutable."""
        guard = StalenessGuard()
        verdict = guard.check(None, symbol="X")
        with pytest.raises(AttributeError):
            verdict.ok = True  # type: ignore[misc]

    def test_completion_error_fails_closed(self):
        """If is_complete() raises, the bar is rejected (fail-closed)."""
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=70, reference_time=now)
        guard = StalenessGuard(max_staleness_s=120)

        # Use a bogus timeframe that is_complete() won't handle
        # MarketDataContract.is_complete maps timeframes; unknown → returns True
        # Actually let's mock is_complete to raise
        from unittest.mock import patch

        with patch.object(type(bar), "is_complete", side_effect=RuntimeError("boom")):
            verdict = guard.check(bar, timeframe="1Min", reference_time=now)

        assert verdict.ok is False
        assert verdict.reason == "completion_check_error"
        assert verdict.event["outcome"] == "REJECTED"

    def test_no_data_event_has_none_age(self):
        """no_data verdict has bar_age_s=None."""
        guard = StalenessGuard()
        verdict = guard.check(None, symbol="X")
        assert verdict.event["bar_age_s"] is None

    def test_threshold_boundary_passes(self):
        """Bar at exactly the threshold passes (> not >=)."""
        now = datetime.now(timezone.utc)
        bar = _bar(age_seconds=65, reference_time=now)
        guard = StalenessGuard(max_staleness_s=65, require_complete=False)

        verdict = guard.check(bar, reference_time=now)
        assert verdict.ok is True  # 65 is NOT > 65
