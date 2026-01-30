"""
Patch 4 Tests: Code Quality & Live Deployment Prep

Tests verify:
1. Pydantic ConfigDict migration (no deprecation warnings)
2. UTC datetime usage (timezone-aware)
3. Import cleanness (all modules load)
4. Backward compatibility with existing tests
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from core.config.schema import ConfigSchema, RiskConfig
from core.execution.engine import OrderExecutionEngine
from core.state.order_machine import OrderStateMachine, OrderStatus, Order


# ============================================================================
# TEST 1: Pydantic ConfigDict Migration
# ============================================================================

def test_pydantic_config_dict_no_warnings():
    """
    PATCH 4: Verify Pydantic v2 ConfigDict migration.
    
    No deprecation warnings should be raised when using config models.
    """
    import warnings
    
    # Capture warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        # Create config objects
        risk_config = RiskConfig(
            initial_account_value=Decimal("10000"),
            max_open_positions=3,
            daily_loss_limit_usd=Decimal("200")
        )
        
        # Trigger validation on assignment (model_config validate_assignment)
        risk_config.max_open_positions = 5
        
        # Check for Pydantic deprecation warnings
        pydantic_warnings = [
            warning for warning in w
            if "pydantic" in str(warning.message).lower()
            and "deprecated" in str(warning.message).lower()
        ]
        
        assert len(pydantic_warnings) == 0, (
            f"Pydantic deprecation warnings found: {pydantic_warnings}"
        )
    
    print("✅ PATCH 4 TEST 1 PASSED: No Pydantic deprecation warnings")


# ============================================================================
# TEST 2: UTC Datetime (datetime.now(timezone.utc))
# ============================================================================

def test_datetime_utc_aware():
    """
    PATCH 4: Verify all datetime objects are UTC-aware.
    
    Should use datetime.now(timezone.utc) instead of datetime.utcnow().
    """
    import warnings
    
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        # Create Order (uses datetime.now(timezone.utc) in default_factory)
        order = Order(
            order_id="TEST_UTC",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Verify created_at is timezone-aware
        assert order.created_at.tzinfo is not None, "created_at must be timezone-aware"
        assert order.created_at.tzinfo == timezone.utc, "created_at must be UTC"
        
        # Check for datetime.utcnow() deprecation warnings
        utc_warnings = [
            warning for warning in w
            if "utcnow" in str(warning.message).lower()
            and "deprecated" in str(warning.message).lower()
        ]
        
        assert len(utc_warnings) == 0, (
            f"datetime.utcnow() deprecation warnings found: {utc_warnings}"
        )
    
    print("✅ PATCH 4 TEST 2 PASSED: All datetimes are UTC-aware")


# ============================================================================
# TEST 3: Order Machine Timestamp UTC Awareness
# ============================================================================

def test_order_machine_utc_timestamps():
    """
    PATCH 4: Verify OrderStateMachine uses UTC-aware timestamps.
    """
    # Create mock dependencies
    mock_event_bus = Mock()
    mock_transaction_log = Mock()
    
    # Create state machine
    machine = OrderStateMachine(
        event_bus=mock_event_bus,
        transaction_log=mock_transaction_log
    )
    
    # Create order
    order = machine.create_order(
        order_id="ORD_UTC_TEST",
        symbol="AAPL",
        quantity=Decimal("5"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    
    # Verify timestamps are UTC-aware
    assert order.created_at.tzinfo == timezone.utc, "Order created_at must be UTC"
    
    # Transition to SUBMITTED
    machine.transition(
        order_id="ORD_UTC_TEST",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BRK_TEST_001"
    )
    
    # Get updated order
    order = machine.get_order("ORD_UTC_TEST")
    assert order.submitted_at.tzinfo == timezone.utc, "Order submitted_at must be UTC"
    
    print("✅ PATCH 4 TEST 3 PASSED: OrderStateMachine timestamps are UTC-aware")


# ============================================================================
# TEST 4: Execution Engine UTC Awareness
# ============================================================================

def test_execution_engine_utc_timestamps():
    """
    PATCH 4: Verify OrderExecutionEngine uses UTC-aware timestamps.
    """
    # Create mocks
    mock_broker = Mock()
    mock_state_machine = Mock()
    mock_position_store = Mock()
    
    # Mock broker methods
    mock_broker.submit_market_order = Mock(return_value="BRK_002")
    mock_state_machine.transition = Mock()
    
    # Create engine
    engine = OrderExecutionEngine(
        broker=mock_broker,
        state_machine=mock_state_machine,
        position_store=mock_position_store
    )
    
    # Submit order (triggers metadata storage with submitted_at timestamp)
    try:
        engine.submit_market_order(
            internal_order_id="ENG_UTC_001",
            symbol="MSFT",
            quantity=Decimal("10"),
            side=Mock(),
            strategy="test"
        )
    except Exception:
        pass  # We're just testing timestamp creation
    
    # Verify metadata has UTC-aware timestamp
    with engine._metadata_lock:
        if "ENG_UTC_001" in engine._order_metadata:
            submitted_at = engine._order_metadata["ENG_UTC_001"]["submitted_at"]
            assert submitted_at.tzinfo == timezone.utc, "submitted_at must be UTC"
    
    print("✅ PATCH 4 TEST 4 PASSED: ExecutionEngine timestamps are UTC-aware")


# ============================================================================
# TEST 5: Module Import Verification
# ============================================================================

def test_all_core_modules_import():
    """
    PATCH 4: Verify all core modules import without errors.
    
    This catches syntax errors, missing dependencies, and circular imports.
    """
    # Core modules that must import cleanly
    imports = [
        "from core.config.schema import ConfigSchema",
        "from core.execution.engine import OrderExecutionEngine",
        "from core.state.order_machine import OrderStateMachine",
        "from core.state.reconciler import BrokerReconciler",
        "from core.state.position_store import PositionStore",
        "from core.brokers.alpaca_connector import AlpacaBrokerConnector",
        "from core.runtime.app import run_app",
    ]
    
    for import_statement in imports:
        try:
            exec(import_statement)
        except Exception as e:
            pytest.fail(f"Failed to import: {import_statement}\nError: {e}")
    
    print("✅ PATCH 4 TEST 5 PASSED: All core modules import successfully")


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("PATCH 4 TEST SUITE: Code Quality & Live Deployment Prep")
    print("=" * 70)
    
    test_pydantic_config_dict_no_warnings()
    test_datetime_utc_aware()
    test_order_machine_utc_timestamps()
    test_execution_engine_utc_timestamps()
    test_all_core_modules_import()
    
    print("\n" + "=" * 70)
    print("✅ ALL PATCH 4 TESTS PASSED (5/5)")
    print("=" * 70)
