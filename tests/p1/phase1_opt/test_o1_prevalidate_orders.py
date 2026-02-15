"""
P1-O1 — Pre-validate Orders Before Submission

INVARIANT:
    When symbol_properties is available and contains properties for the
    target symbol, the execution engine MUST validate quantity, price,
    and tradability BEFORE submitting the order to the broker.

    When symbol_properties is None or the symbol is not cached, the
    engine MUST skip validation gracefully (no crash) and proceed to
    submission.

    OrderValidationError MUST be a proper exception class inheriting
    from OrderExecutionError so that callers can catch it specifically.

TESTS:
    1. OrderValidationError exists and inherits OrderExecutionError
    2. Market order: untradable symbol → OrderValidationError before broker call
    3. Market order: quantity below minimum → OrderValidationError
    4. Market order: valid order → no error, broker called
    5. Market order: no symbol_properties → skip validation, broker called
    6. Market order: symbol not in cache → skip validation, broker called
    7. Limit order: negative price → OrderValidationError
    8. Limit order: quantity validation fires → OrderValidationError
    9. Quantity rounding: lot_size > 1 → quantity rounded down before submit
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from core.execution.engine import (
    OrderExecutionEngine,
    OrderExecutionError,
    DuplicateOrderError,
)
from core.market.symbol_properties import SymbolProperties, SymbolPropertiesCache
from core.brokers.alpaca_connector import BrokerOrderSide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_cache(props_dict: dict):
    """Build a minimal SymbolPropertiesCache-like object."""
    cache = MagicMock(spec=SymbolPropertiesCache)
    cache.get = lambda sym: props_dict.get(sym)
    return cache


def _make_engine(symbol_properties=None, broker=None):
    """Construct an engine with mocked collaborators."""
    broker = broker or MagicMock()
    broker.submit_order = MagicMock(return_value="BRK-001")
    broker.submit_limit_order = MagicMock(return_value="BRK-002")

    state_machine = MagicMock()
    position_store = MagicMock()

    engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store,
        symbol_properties=symbol_properties,
    )
    return engine, broker


# ===================================================================
# 1. Exception class existence
# ===================================================================

class TestOrderValidationErrorExists:

    def test_class_exists_and_importable(self):
        from core.execution.engine import OrderValidationError
        assert OrderValidationError is not None

    def test_inherits_order_execution_error(self):
        from core.execution.engine import OrderValidationError
        assert issubclass(OrderValidationError, OrderExecutionError)

    def test_can_be_raised_and_caught(self):
        from core.execution.engine import OrderValidationError
        with pytest.raises(OrderValidationError):
            raise OrderValidationError("test")


# ===================================================================
# 2. Market order validation fires when props available
# ===================================================================

class TestMarketOrderPreValidation:

    def test_untradable_symbol_raises(self):
        """Untradable symbol → OrderValidationError, broker NOT called."""
        from core.execution.engine import OrderValidationError

        props = SymbolProperties(symbol="DELISTED", is_tradable=False)
        cache = _fake_cache({"DELISTED": props})
        engine, broker = _make_engine(symbol_properties=cache)

        with pytest.raises(OrderValidationError, match="not tradable"):
            engine.submit_market_order(
                internal_order_id="ORD-001",
                symbol="DELISTED",
                quantity=Decimal("10"),
                side=BrokerOrderSide.BUY,
                strategy="Test",
            )

        broker.submit_order.assert_not_called()

    def test_quantity_below_minimum_raises(self):
        """Quantity below min_order_size → OrderValidationError."""
        from core.execution.engine import OrderValidationError

        props = SymbolProperties(symbol="SPY", min_order_size=5)
        cache = _fake_cache({"SPY": props})
        engine, broker = _make_engine(symbol_properties=cache)

        with pytest.raises(OrderValidationError, match="below minimum"):
            engine.submit_market_order(
                internal_order_id="ORD-002",
                symbol="SPY",
                quantity=Decimal("2"),
                side=BrokerOrderSide.BUY,
                strategy="Test",
            )

        broker.submit_order.assert_not_called()

    def test_valid_order_passes_to_broker(self):
        """Valid order → no error, broker.submit_order called."""
        props = SymbolProperties(symbol="SPY", min_order_size=1)
        cache = _fake_cache({"SPY": props})
        engine, broker = _make_engine(symbol_properties=cache)

        result = engine.submit_market_order(
            internal_order_id="ORD-003",
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="Test",
        )

        assert result is not None  # got a broker_order_id back


# ===================================================================
# 3. Graceful skip when no symbol_properties
# ===================================================================

class TestGracefulSkipNoProperties:

    def test_no_symbol_properties_skips_validation(self):
        """symbol_properties=None → order proceeds to broker."""
        engine, broker = _make_engine(symbol_properties=None)

        result = engine.submit_market_order(
            internal_order_id="ORD-004",
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="Test",
        )

        assert result is not None

    def test_symbol_not_in_cache_skips_validation(self):
        """Symbol not cached → validation skipped, order proceeds."""
        cache = _fake_cache({})  # empty cache
        engine, broker = _make_engine(symbol_properties=cache)

        result = engine.submit_market_order(
            internal_order_id="ORD-005",
            symbol="UNKNOWN",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="Test",
        )

        assert result is not None


# ===================================================================
# 4. Limit order validation
# ===================================================================

class TestLimitOrderPreValidation:

    def test_negative_price_raises(self):
        """limit_price <= 0 → OrderValidationError."""
        from core.execution.engine import OrderValidationError

        engine, broker = _make_engine(symbol_properties=None)

        with pytest.raises(OrderValidationError, match="positive"):
            engine.submit_limit_order(
                internal_order_id="ORD-006",
                symbol="SPY",
                quantity=Decimal("10"),
                side=BrokerOrderSide.BUY,
                limit_price=Decimal("-5"),
                strategy="Test",
            )

    def test_quantity_validation_fires_for_limit(self):
        """Quantity below minimum on limit order → OrderValidationError."""
        from core.execution.engine import OrderValidationError

        props = SymbolProperties(symbol="SPY", min_order_size=10)
        cache = _fake_cache({"SPY": props})
        engine, broker = _make_engine(symbol_properties=cache)

        with pytest.raises(OrderValidationError, match="below minimum"):
            engine.submit_limit_order(
                internal_order_id="ORD-007",
                symbol="SPY",
                quantity=Decimal("2"),
                side=BrokerOrderSide.BUY,
                limit_price=Decimal("450.00"),
                strategy="Test",
            )


# ===================================================================
# 5. Quantity rounding via lot_size
# ===================================================================

class TestQuantityRounding:

    def test_aligned_quantity_passes(self):
        """Quantity that IS aligned to lot_size → passes validation."""
        props = SymbolProperties(symbol="SPY", lot_size=100, min_order_size=100)
        cache = _fake_cache({"SPY": props})
        engine, broker = _make_engine(symbol_properties=cache)

        result = engine.submit_market_order(
            internal_order_id="ORD-008",
            symbol="SPY",
            quantity=Decimal("200"),
            side=BrokerOrderSide.BUY,
            strategy="Test",
        )

        assert result is not None

    def test_misaligned_quantity_rejected(self):
        """PATCH 10: Quantity not aligned to lot_size → auto-rounded, not rejected."""
        props = SymbolProperties(symbol="SPY", lot_size=100, min_order_size=100)
        cache = _fake_cache({"SPY": props})
        engine, broker = _make_engine(symbol_properties=cache)

        # PATCH 10: quantity 250 is rounded to 200 (nearest lot)
        engine.submit_market_order(
            internal_order_id="ORD-009",
            symbol="SPY",
            quantity=Decimal("250"),
            side=BrokerOrderSide.BUY,
            strategy="Test",
        )

        # Verify broker received rounded quantity
        broker.submit_market_order.assert_called_once()
        call_kwargs = broker.submit_market_order.call_args.kwargs
        assert call_kwargs["quantity"] == Decimal("200")
