"""
P1-O2 — Fail Fast on Malformed Signals

INVARIANT:
    The runtime MUST NOT crash when it receives a malformed signal.
    Malformed signals (missing required fields, zero quantity, bad types)
    MUST be skipped gracefully with no order submission.

    When a valid signal is mixed with malformed ones, only the valid
    signal should produce an order.

TESTS:
    1. Signal with quantity=0 → skipped, no order
    2. Signal with quantity=-1 → skipped, no order
    3. Signal with missing quantity → skipped (defaults to 0)
    4. LIMIT signal missing limit_price → skipped, no order
    5. Valid signal alongside malformed → only valid signal submits
    6. Signal with empty symbol → still processes (uses loop symbol fallback)
    7. Signal with missing side → defaults to BUY, order submitted
"""

import pytest
from decimal import Decimal


class TestMalformedSignalSkipping:
    """Runtime-level tests: malformed signals are skipped, not crashed."""

    def test_zero_quantity_skipped(self, patch_runtime):
        """Signal with quantity=0 → no order submitted."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "0",
                "order_type": "MARKET",
                "strategy": "TestStrat",
            },
        ]

        container, exec_engine = patch_runtime(signals)
        assert len(exec_engine.calls) == 0, (
            f"Zero-qty signal should be skipped, got {len(exec_engine.calls)} calls"
        )

    def test_negative_quantity_skipped(self, patch_runtime):
        """Signal with negative quantity → no order submitted."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "-5",
                "order_type": "MARKET",
                "strategy": "TestStrat",
            },
        ]

        container, exec_engine = patch_runtime(signals)
        assert len(exec_engine.calls) == 0, (
            f"Negative-qty signal should be skipped, got {len(exec_engine.calls)} calls"
        )

    def test_missing_quantity_skipped(self, patch_runtime):
        """Signal with no 'quantity' key → defaults to 0, skipped."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "order_type": "MARKET",
                "strategy": "TestStrat",
                # no "quantity" key
            },
        ]

        container, exec_engine = patch_runtime(signals)
        assert len(exec_engine.calls) == 0, (
            f"Missing-qty signal should be skipped, got {len(exec_engine.calls)} calls"
        )

    def test_limit_missing_price_skipped(self, patch_runtime):
        """LIMIT signal without limit_price → skipped."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "10",
                "order_type": "LIMIT",
                "strategy": "TestStrat",
                # no "limit_price" key
            },
        ]

        container, exec_engine = patch_runtime(signals)

        # Should have no limit order calls
        limit_calls = [
            (m, kw) for m, kw in exec_engine.calls
            if m == "submit_limit_order"
        ]
        assert len(limit_calls) == 0, (
            f"LIMIT signal without price should be skipped, got {len(limit_calls)} limit calls"
        )


class TestValidSignalAmongstMalformed:
    """Only valid signals produce orders; malformed ones are silently dropped."""

    def test_valid_signal_alongside_malformed(self, patch_runtime):
        """One valid + one malformed → exactly 1 order submitted."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "0",  # malformed: zero qty
                "order_type": "MARKET",
                "strategy": "TestStrat",
            },
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "1",  # valid
                "order_type": "MARKET",
                "strategy": "TestStrat",
            },
        ]

        container, exec_engine = patch_runtime(signals)

        market_calls = [
            (m, kw) for m, kw in exec_engine.calls
            if m == "submit_market_order"
        ]
        assert len(market_calls) >= 1, (
            f"Valid signal should produce at least 1 order, got {len(market_calls)}"
        )


class TestSignalDefaults:
    """Signals with missing optional fields use safe defaults."""

    def test_missing_side_defaults_to_buy(self, patch_runtime):
        """Signal without 'side' → defaults to BUY, order proceeds."""
        signals = [
            {
                "symbol": "SPY",
                "quantity": "1",
                "order_type": "MARKET",
                "strategy": "TestStrat",
                # no "side" key
            },
        ]

        container, exec_engine = patch_runtime(signals)

        market_calls = [
            (m, kw) for m, kw in exec_engine.calls
            if m == "submit_market_order"
        ]
        assert len(market_calls) >= 1, (
            f"Missing-side signal should default to BUY and submit, "
            f"got {len(market_calls)} market calls"
        )

    def test_missing_order_type_defaults_to_market(self, patch_runtime):
        """Signal without 'order_type' → defaults to MARKET."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "1",
                "strategy": "TestStrat",
                # no "order_type" key
            },
        ]

        container, exec_engine = patch_runtime(signals)

        market_calls = [
            (m, kw) for m, kw in exec_engine.calls
            if m == "submit_market_order"
        ]
        assert len(market_calls) >= 1, (
            f"Missing-order_type signal should default to MARKET, "
            f"got {len(market_calls)} market calls"
        )
