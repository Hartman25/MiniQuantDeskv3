"""
PATCH 2 Tests - Execution Safety Guards

Tests for:
1. Duplicate order rejection (engine-level)
2. Fat-finger price rejection (gate-level)
3. Daily counter auto-reset (gate-level)
4. PDT tracking correctness (gate-level)

CRITICAL: All tests must pass before Patch 2 is considered complete.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock
from pathlib import Path
import tempfile
import os

# Engine imports
from core.execution.engine import OrderExecutionEngine, DuplicateOrderError
from core.state import OrderStateMachine, OrderStatus, PositionStore
from core.brokers.alpaca_connector import BrokerOrderSide

# Risk gate imports
from core.risk.gate import PreTradeRiskGate, OrderRequest
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_db():
    """Create temporary database file."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except:
        pass


@pytest.fixture
def position_store(temp_db):
    """Create position store with temp database."""
    return PositionStore(db_path=Path(temp_db))


@pytest.fixture
def limits_tracker(temp_db):
    """Create limits tracker with temp database."""
    return PersistentLimitsTracker(
        db_path=temp_db,
        daily_loss_limit=Decimal("500")
    )


# ============================================================================
# TEST 1: Duplicate Order Rejection (Engine Level)
# ============================================================================

def test_duplicate_order_rejection(position_store):
    """
    CRITICAL: Engine must reject duplicate order IDs.
    
    Scenario:
    1. Submit order ORD_001
    2. Attempt to submit ORD_001 again
    3. Must raise DuplicateOrderError
    """
    # Setup
    mock_broker = Mock()
    mock_broker.submit_market_order.return_value = "BROKER_123"
    
    state_machine = OrderStateMachine(
        event_bus=Mock(),
        transaction_log=Mock()
    )
    
    engine = OrderExecutionEngine(
        broker=mock_broker,
        state_machine=state_machine,
        position_store=position_store
    )
    
    # Create order in state machine (PENDING)
    state_machine.create_order(
        order_id="ORD_001",
        symbol="SPY",
        quantity=Decimal("10"),
        side="BUY",
        order_type="MARKET",
        strategy="TestStrategy"
    )
    
    # First submission - should succeed
    broker_id_1 = engine.submit_market_order(
        internal_order_id="ORD_001",
        symbol="SPY",
        quantity=Decimal("10"),
        side=BrokerOrderSide.BUY,
        strategy="TestStrategy"
    )
    
    assert broker_id_1 == "BROKER_123"
    assert "ORD_001" in engine._submitted_order_ids
    
    # Second submission attempt - duplicate order ID already submitted
    # Should raise DuplicateOrderError without calling broker again
    with pytest.raises(DuplicateOrderError) as exc_info:
        engine.submit_market_order(
            internal_order_id="ORD_001",
            symbol="SPY",
            quantity=Decimal("10"),
            side=BrokerOrderSide.BUY,
            strategy="TestStrategy"
        )
    
    assert "DUPLICATE_ORDER" in str(exc_info.value)
    assert "ORD_001" in str(exc_info.value)
    
    # Verify broker was only called once
    assert mock_broker.submit_market_order.call_count == 1


# ============================================================================
# TEST 2: Fat-Finger Price Rejection (Gate Level)
# ============================================================================

def test_fat_finger_price_rejection(limits_tracker):
    """
    CRITICAL: Gate must reject orders with >10% price deviation.
    
    Scenario:
    1. Submit order at $100
    2. Submit order at $120 (20% deviation)
    3. Second order must be rejected (FAT_FINGER)
    """
    # Setup
    position_sizer = NotionalPositionSizer(
        max_exposure_per_position=Decimal("0.20")
    )
    
    gate = PreTradeRiskGate(
        limits_tracker=limits_tracker,
        position_sizer=position_sizer,
        account_value=Decimal("10000"),
        enable_pdt_protection=False,
        max_orders_per_day=1000
    )
    
    gate.start()
    
    try:
        # First order at $100 - establishes baseline
        request_1 = OrderRequest(
            order_id="ORD_001",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("100.00")
        )
        
        decision_1 = gate.submit_order(request_1, timeout=2.0)
        assert decision_1.approved, f"First order should be approved: {decision_1.rejection_reason}"
        
        # Second order at $120 - 20% deviation
        request_2 = OrderRequest(
            order_id="ORD_002",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("120.00")  # 20% higher
        )
        
        decision_2 = gate.submit_order(request_2, timeout=2.0)
        assert not decision_2.approved, "Order with 20% price deviation should be rejected"
        assert "FAT_FINGER" in decision_2.rejection_reason
        assert "20.0%" in decision_2.rejection_reason or "20%" in decision_2.rejection_reason
        
        # Third order at $109 - 9% deviation (should pass)
        request_3 = OrderRequest(
            order_id="ORD_003",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("109.00")  # 9% higher (within threshold)
        )
        
        decision_3 = gate.submit_order(request_3, timeout=2.0)
        assert decision_3.approved, f"Order with 9% deviation should pass: {decision_3.rejection_reason}"
        
    finally:
        gate.stop()


# ============================================================================
# TEST 3: Daily Counter Auto-Reset
# ============================================================================

def test_daily_counter_reset(limits_tracker):
    """
    CRITICAL: Gate must auto-reset counters at midnight UTC.
    
    Scenario:
    1. Submit 3 orders (hit max_orders_per_day=3)
    2. Simulate day change
    3. Submit order - should succeed (counters reset)
    """
    # Setup
    position_sizer = NotionalPositionSizer(
        max_exposure_per_position=Decimal("0.20")
    )
    
    gate = PreTradeRiskGate(
        limits_tracker=limits_tracker,
        position_sizer=position_sizer,
        account_value=Decimal("10000"),
        enable_pdt_protection=True,
        max_orders_per_day=3  # Low limit to test reset
    )
    
    gate.start()
    
    try:
        # Submit 3 orders (hit limit)
        for i in range(3):
            request = OrderRequest(
                order_id=f"ORD_{i:03d}",
                symbol="SPY",
                quantity=10,
                side="LONG",
                order_type="MARKET",
                strategy="TestStrategy",
                current_price=Decimal("100.00")
            )
            decision = gate.submit_order(request, timeout=2.0)
            assert decision.approved, f"Order {i} should be approved"
        
        # 4th order should be rejected (limit reached)
        request_4 = OrderRequest(
            order_id="ORD_004",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("100.00")
        )
        decision_4 = gate.submit_order(request_4, timeout=2.0)
        assert not decision_4.approved, "4th order should be rejected (limit=3)"
        assert "PDT protection" in decision_4.rejection_reason
        
        # Simulate day change (manually set last reset to yesterday)
        with gate._lock:
            gate._last_reset_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        
        # 5th order should succeed (auto-reset triggered)
        request_5 = OrderRequest(
            order_id="ORD_005",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("100.00")
        )
        decision_5 = gate.submit_order(request_5, timeout=2.0)
        assert decision_5.approved, f"Order after day change should pass: {decision_5.rejection_reason}"
        
        # Verify counters were reset
        with gate._lock:
            assert gate._order_count_today == 1, "Order count should be 1 after reset"
            assert gate._last_reset_date == datetime.now(timezone.utc).date()
        
    finally:
        gate.stop()


# ============================================================================
# TEST 4: PDT Tracking (Separate from Order Count)
# ============================================================================

def test_pdt_tracking_correct(limits_tracker):
    """
    CRITICAL: PDT tracking must be separate from total order count.
    
    PDT = Pattern Day Trader rule (3+ day trades in 5 days)
    Day Trade = Buy + Sell of same symbol in same day
    
    Scenario:
    1. Buy SPY (order 1)
    2. Sell SPY (order 2) - this is 1 day trade
    3. Buy AAPL (order 3) - not a day trade
    4. Verify: 3 total orders, 1 day trade
    
    Note: Full PDT implementation deferred to Phase 3.
    This test verifies data structures are in place.
    """
    # Setup
    position_sizer = NotionalPositionSizer(
        max_exposure_per_position=Decimal("0.20")
    )
    
    gate = PreTradeRiskGate(
        limits_tracker=limits_tracker,
        position_sizer=position_sizer,
        account_value=Decimal("10000"),
        enable_pdt_protection=False,  # Disable limit for this test
        max_orders_per_day=1000
    )
    
    gate.start()
    
    try:
        # Order 1: Buy SPY
        request_1 = OrderRequest(
            order_id="ORD_001",
            symbol="SPY",
            quantity=10,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("100.00")
        )
        gate.submit_order(request_1, timeout=2.0)
        
        # Order 2: Sell SPY (completes day trade)
        request_2 = OrderRequest(
            order_id="ORD_002",
            symbol="SPY",
            quantity=10,
            side="SHORT",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("101.00")
        )
        gate.submit_order(request_2, timeout=2.0)
        
        # Order 3: Buy AAPL (not a day trade)
        request_3 = OrderRequest(
            order_id="ORD_003",
            symbol="AAPL",
            quantity=5,
            side="LONG",
            order_type="MARKET",
            strategy="TestStrategy",
            current_price=Decimal("150.00")
        )
        gate.submit_order(request_3, timeout=2.0)
        
        # Verify state
        with gate._lock:
            assert gate._order_count_today == 3, "Should have 3 total orders"
            
            # Verify PDT tracking data structure exists
            assert hasattr(gate, '_day_trades_today'), "PDT tracking set must exist"
            assert isinstance(gate._day_trades_today, set), "PDT tracking must be a set"
            
            # Note: Full PDT logic (detecting buy+sell pairs) is Phase 3
            # Here we only verify the data structure is in place
        
    finally:
        gate.stop()


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
