"""
P1-B2 — Single Active Position Enforcement

INVARIANT:
    For any given symbol, the system MUST NOT submit a second BUY order
    when there is already an open position.  This prevents:
    - Double-entry (pyramiding) which is not supported by the strategy spec
    - Protective stop confusion (which position does the stop protect?)

    The enforcement happens at two layers:
    1. Runtime loop: checks position_store before submitting BUY
    2. _single_trade_should_block_entry(): queries position_store + broker

HALT DECISION:
    Block (skip signal), do NOT halt.  A duplicate entry signal is not
    an invariant violation — it's an expected edge case (e.g. signal
    replay).  The correct response is to log and skip.

TESTS:
    - Two BUY signals in same cycle → only 1 entry order submitted
    - BUY signal when position already exists → blocked
    - SELL signal still goes through when position exists
    - _single_trade_should_block_entry direct unit tests
"""

import pytest
from decimal import Decimal
from types import SimpleNamespace


# ===================================================================
# 1. Runtime integration: two BUY signals → only one entry
# ===================================================================

class TestSinglePositionRuntime:
    """Runtime-level tests using patch_runtime fixture."""

    def test_second_buy_signal_blocked_when_first_fills(self, patch_runtime):
        """Two BUY signals in same cycle: only one BUY order submitted.

        The first BUY creates a position (FakeExecEngine returns FILLED).
        The second BUY for the same symbol must be blocked.
        """
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "1",
                "order_type": "MARKET",
                "strategy": "VWAPMicroMeanReversion",
            },
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "1",
                "order_type": "MARKET",
                "strategy": "VWAPMicroMeanReversion",
            },
        ]

        container, exec_engine = patch_runtime(signals)

        spy_orders = exec_engine.get_orders_by_symbol("SPY")
        buy_entries = [
            (otype, kw) for otype, kw in spy_orders
            if otype == "MARKET" and kw["side"].value == "BUY"
        ]
        assert len(buy_entries) == 1, (
            f"Expected exactly 1 BUY entry order, got {len(buy_entries)}: {buy_entries}"
        )

    def test_buy_blocked_when_position_exists(self, patch_runtime):
        """BUY signal when position already exists → no order submitted."""
        signals = [
            {
                "symbol": "SPY",
                "side": "BUY",
                "quantity": "1",
                "order_type": "MARKET",
                "strategy": "VWAPMicroMeanReversion",
            },
        ]

        # Pre-populate position store with 1 share
        container, exec_engine = patch_runtime(
            signals, position_qty=Decimal("1"),
        )

        spy_orders = exec_engine.get_orders_by_symbol("SPY")
        buy_entries = [
            (otype, kw) for otype, kw in spy_orders
            if otype == "MARKET" and kw["side"].value == "BUY"
        ]
        assert len(buy_entries) == 0, (
            f"Should not enter when already in position, got {len(buy_entries)} BUY orders"
        )

    def test_sell_allowed_when_position_exists(self, patch_runtime):
        """SELL signal when position exists → sell order IS submitted."""
        signals = [
            {
                "symbol": "SPY",
                "side": "SELL",
                "quantity": "1",
                "order_type": "MARKET",
                "strategy": "VWAPMicroMeanReversion",
            },
        ]

        container, exec_engine = patch_runtime(
            signals, position_qty=Decimal("1"),
        )

        spy_orders = exec_engine.get_orders_by_symbol("SPY")
        sell_orders = [
            (otype, kw) for otype, kw in spy_orders
            if otype == "MARKET" and kw["side"].value == "SELL"
        ]
        assert len(sell_orders) >= 1, (
            f"SELL should be allowed when position exists, got {len(sell_orders)} SELL orders"
        )


# ===================================================================
# 2. Direct unit tests for _single_trade_should_block_entry
# ===================================================================

class TestSingleTradeBlockEntry:
    """Unit tests for the _single_trade_should_block_entry helper."""

    def test_blocks_when_position_store_has_open_position(self):
        from core.runtime.app import _single_trade_should_block_entry

        class _PS:
            def has_open_position(self, sym):
                return sym == "SPY"

        assert _single_trade_should_block_entry("SPY", position_store=_PS()) is True

    def test_allows_when_position_store_empty(self):
        from core.runtime.app import _single_trade_should_block_entry

        class _PS:
            def has_open_position(self, sym):
                return False

        assert _single_trade_should_block_entry("SPY", position_store=_PS()) is False

    def test_blocks_when_broker_has_position(self):
        from core.runtime.app import _single_trade_should_block_entry

        class _Broker:
            def get_positions(self):
                return [SimpleNamespace(symbol="SPY", qty="5")]

        # No position_store, but broker has position
        assert _single_trade_should_block_entry(
            "SPY", broker=_Broker(), position_store=None,
        ) is True

    def test_allows_when_no_position_anywhere(self):
        from core.runtime.app import _single_trade_should_block_entry

        class _PS:
            def has_open_position(self, sym):
                return False

        class _Broker:
            def get_positions(self):
                return []
            def list_open_orders(self):
                return []

        assert _single_trade_should_block_entry(
            "SPY", broker=_Broker(), position_store=_PS(),
        ) is False

    def test_case_insensitive_symbol_match(self):
        from core.runtime.app import _single_trade_should_block_entry

        class _PS:
            def has_open_position(self, sym):
                return sym == "SPY"

        assert _single_trade_should_block_entry("spy", position_store=_PS()) is True
        assert _single_trade_should_block_entry("Spy", position_store=_PS()) is True

    def test_fail_open_by_default(self):
        """If all checks explode, default is fail-open (return False)."""
        from core.runtime.app import _single_trade_should_block_entry

        class _BadPS:
            def has_open_position(self, sym):
                raise RuntimeError("boom")

        assert _single_trade_should_block_entry(
            "SPY", position_store=_BadPS(), fail_closed=False,
        ) is False

    def test_fail_closed_when_requested(self):
        """With fail_closed=True, explosion → return True (block)."""
        from core.runtime.app import _single_trade_should_block_entry

        class _BadPS:
            def has_open_position(self, sym):
                raise RuntimeError("boom")

        assert _single_trade_should_block_entry(
            "SPY", position_store=_BadPS(), fail_closed=True,
        ) is True
