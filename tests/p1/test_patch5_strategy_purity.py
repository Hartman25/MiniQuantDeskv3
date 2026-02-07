"""
PATCH 5 — Strategy decision purity enforcement

INVARIANT:
    1. on_bar() must return None, dict, StrategySignal, or list thereof.
       Anything else raises StrategyPurityError immediately.
    2. Strategies must NOT hold broker/engine references.
       check_broker_access() raises StrategyPurityError on violation.
    3. Lifecycle manager calls both checks (broker on start, output on bar).

TESTS:
    1.  validate_signal_output: None → []
    2.  validate_signal_output: dict → [dict]
    3.  validate_signal_output: list[dict] → passthrough
    4.  validate_signal_output: bad type → StrategyPurityError
    5.  validate_signal_output: list with bad item → StrategyPurityError
    6.  check_broker_access: clean strategy passes
    7.  check_broker_access: broker attribute → StrategyPurityError
    8.  check_broker_access: _exec_engine attribute → StrategyPurityError
    9.  Lifecycle: bad-return strategy raises on on_bar()
   10.  Lifecycle: broker-holding strategy raises on start_strategy()
   11.  Lifecycle: good strategy works normally
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional

from strategies.base import (
    IStrategy,
    StrategyPurityError,
    check_broker_access,
    validate_signal_output,
)
from strategies.lifecycle import StrategyLifecycleManager
from core.data.contract import MarketDataContract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(symbol="SPY") -> MarketDataContract:
    return MarketDataContract(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.50"),
        volume=1000,
        provider="test",
    )


class _GoodStrategy(IStrategy):
    """Returns a proper dict signal."""

    def on_init(self):
        pass

    def on_bar(self, bar):
        return {
            "symbol": bar.symbol,
            "side": "BUY",
            "quantity": Decimal("1"),
            "order_type": "MARKET",
            "strategy": self.name,
        }


class _NoneStrategy(IStrategy):
    """Returns None (no signal this bar)."""

    def on_init(self):
        pass

    def on_bar(self, bar):
        return None


class _ListStrategy(IStrategy):
    """Returns a list of dicts."""

    def on_init(self):
        pass

    def on_bar(self, bar):
        return [
            {"symbol": bar.symbol, "side": "BUY", "quantity": Decimal("1"),
             "order_type": "MARKET", "strategy": self.name},
            {"symbol": bar.symbol, "side": "SELL", "quantity": Decimal("1"),
             "order_type": "MARKET", "strategy": self.name},
        ]


class _BadReturnStrategy(IStrategy):
    """Returns an integer — purity violation."""

    def on_init(self):
        pass

    def on_bar(self, bar):
        return 42  # BAD


class _BadListItemStrategy(IStrategy):
    """Returns a list with a non-dict item."""

    def on_init(self):
        pass

    def on_bar(self, bar):
        return [{"symbol": "SPY", "side": "BUY"}, "not_a_dict"]


class _BrokerHoldingStrategy(IStrategy):
    """Holds a broker reference — purity violation."""

    def on_init(self):
        self.broker = object()  # simulates storing a broker connector

    def on_bar(self, bar):
        return None


class _ExecEngineHoldingStrategy(IStrategy):
    """Holds an _exec_engine reference — purity violation."""

    def on_init(self):
        self._exec_engine = object()

    def on_bar(self, bar):
        return None


# ---------------------------------------------------------------------------
# Tests: validate_signal_output
# ---------------------------------------------------------------------------

class TestValidateSignalOutput:
    """Pure validation of on_bar return values."""

    def test_none_returns_empty_list(self):
        assert validate_signal_output(None) == []

    def test_dict_returns_single_item_list(self):
        d = {"symbol": "SPY", "side": "BUY"}
        result = validate_signal_output(d)
        assert result == [d]

    def test_list_of_dicts_passthrough(self):
        items = [{"a": 1}, {"b": 2}]
        assert validate_signal_output(items) == items

    def test_empty_list_returns_empty(self):
        assert validate_signal_output([]) == []

    def test_integer_raises(self):
        with pytest.raises(StrategyPurityError, match="unsupported type.*int"):
            validate_signal_output(42, strategy_name="BadStrat")

    def test_string_raises(self):
        with pytest.raises(StrategyPurityError, match="unsupported type.*str"):
            validate_signal_output("nope")

    def test_list_with_bad_item_raises(self):
        with pytest.raises(StrategyPurityError, match="invalid type at index 1"):
            validate_signal_output([{"ok": True}, 999])

    def test_tuple_of_dicts_ok(self):
        items = ({"a": 1},)
        assert validate_signal_output(items) == [{"a": 1}]


# ---------------------------------------------------------------------------
# Tests: check_broker_access
# ---------------------------------------------------------------------------

class TestCheckBrokerAccess:
    """Detect broker/engine attributes on strategy instances."""

    def test_clean_strategy_passes(self):
        s = _GoodStrategy(name="Clean", config={}, symbols=["SPY"])
        # Should not raise
        check_broker_access(s)

    def test_broker_attribute_raises(self):
        s = _BrokerHoldingStrategy(name="Bad", config={}, symbols=["SPY"])
        s.on_init()  # sets self.broker
        with pytest.raises(StrategyPurityError, match="broker"):
            check_broker_access(s)

    def test_exec_engine_attribute_raises(self):
        s = _ExecEngineHoldingStrategy(name="Bad2", config={}, symbols=["SPY"])
        s.on_init()  # sets self._exec_engine
        with pytest.raises(StrategyPurityError, match="_exec_engine"):
            check_broker_access(s)

    def test_none_valued_attribute_allowed(self):
        """Attribute exists but is None — not a violation."""
        s = _GoodStrategy(name="NoneAttr", config={}, symbols=["SPY"])
        s.broker = None  # explicitly None → OK
        check_broker_access(s)  # should not raise


# ---------------------------------------------------------------------------
# Tests: StrategyLifecycleManager integration
# ---------------------------------------------------------------------------

class TestLifecyclePurityIntegration:
    """Lifecycle manager enforces purity at start and on_bar."""

    def test_good_strategy_works(self):
        lm = StrategyLifecycleManager()
        s = _GoodStrategy(name="Good", config={}, symbols=["SPY"])
        lm.add_strategy(s)
        lm.start_strategy("Good")

        signals = lm.on_bar(_bar("SPY"))
        assert len(signals) == 1
        assert signals[0]["side"] == "BUY"

    def test_none_strategy_returns_empty(self):
        lm = StrategyLifecycleManager()
        s = _NoneStrategy(name="NoSig", config={}, symbols=["SPY"])
        lm.add_strategy(s)
        lm.start_strategy("NoSig")

        signals = lm.on_bar(_bar("SPY"))
        assert signals == []

    def test_list_strategy_returns_multiple(self):
        lm = StrategyLifecycleManager()
        s = _ListStrategy(name="Multi", config={}, symbols=["SPY"])
        lm.add_strategy(s)
        lm.start_strategy("Multi")

        signals = lm.on_bar(_bar("SPY"))
        assert len(signals) == 2

    def test_bad_return_raises_on_bar(self):
        lm = StrategyLifecycleManager()
        s = _BadReturnStrategy(name="BadRet", config={}, symbols=["SPY"])
        lm.add_strategy(s)
        lm.start_strategy("BadRet")

        with pytest.raises(StrategyPurityError, match="unsupported type.*int"):
            lm.on_bar(_bar("SPY"))

    def test_bad_list_item_raises_on_bar(self):
        lm = StrategyLifecycleManager()
        s = _BadListItemStrategy(name="BadList", config={}, symbols=["SPY"])
        lm.add_strategy(s)
        lm.start_strategy("BadList")

        with pytest.raises(StrategyPurityError, match="invalid type"):
            lm.on_bar(_bar("SPY"))

    def test_broker_holding_raises_on_start(self):
        lm = StrategyLifecycleManager()
        s = _BrokerHoldingStrategy(name="BrokerBad", config={}, symbols=["SPY"])
        lm.add_strategy(s)

        with pytest.raises(StrategyPurityError, match="broker"):
            lm.start_strategy("BrokerBad")

    def test_exec_engine_holding_raises_on_start(self):
        lm = StrategyLifecycleManager()
        s = _ExecEngineHoldingStrategy(name="EngineBad", config={}, symbols=["SPY"])
        lm.add_strategy(s)

        with pytest.raises(StrategyPurityError, match="_exec_engine"):
            lm.start_strategy("EngineBad")
