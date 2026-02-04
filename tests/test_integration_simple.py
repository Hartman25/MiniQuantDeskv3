"""
FOCUSED INTEGRATION TESTS - Core Functionality Only
Tests the critical OrderStateMachine integration without complex setup.
"""

import sys
sys.path.insert(0, r'C:\Users\Zacha\Desktop\MiniQuantDeskv2')

from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time

from core.state.order_machine import OrderStateMachine, OrderStatus, Order
from core.state.transaction_log import TransactionLog
from core.events.bus import OrderEventBus


def test_1_order_creation_and_retrieval():
    """Test OrderStateMachine stores and retrieves orders."""
    print("\n[TEST 1] Order Creation and Retrieval...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create order
    order = order_machine.create_order(
        order_id="TEST_001",
        symbol="SPY",
        quantity=Decimal("10"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    
    # Verify creation
    assert order.order_id == "TEST_001"
    assert order.state == OrderStatus.PENDING
    print("  - Order created successfully")
    
    # Retrieve order
    retrieved = order_machine.get_order("TEST_001")
    assert retrieved is not None
    assert retrieved.order_id == "TEST_001"
    print("  - Order retrieved successfully")
    
    # Get all orders
    all_orders = order_machine.get_all_orders()
    assert len(all_orders) == 1
    print("  - Get all orders works")
    
    # Get pending orders
    pending = order_machine.get_pending_orders()
    assert len(pending) == 1
    print("  - Get pending orders works")
    
    print("[PASS] Test 1 Complete")
    assert True


def test_2_state_transitions():
    """Test state transitions update stored orders."""
    print("\n[TEST 2] State Transitions...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()  # Start event bus
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create order
    order = order_machine.create_order(
        order_id="TEST_002",
        symbol="SPY",
        quantity=Decimal("10"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    print("  - Order created")
    
    # Transition to SUBMITTED
    order_machine.transition(
        order_id="TEST_002",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BROKER_123"
    )
    
    # Verify update
    order = order_machine.get_order("TEST_002")
    assert order.state == OrderStatus.SUBMITTED
    assert order.broker_order_id == "BROKER_123"
    assert order.submitted_at is not None
    print("  - PENDING -> SUBMITTED transition works")
    
    # Transition to FILLED
    order_machine.transition(
        order_id="TEST_002",
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.FILLED,
        broker_order_id="BROKER_123",
        filled_qty=Decimal("10"),
        fill_price=Decimal("450.50")
    )
    
    # Verify final state
    order = order_machine.get_order("TEST_002")
    assert order.state == OrderStatus.FILLED
    assert order.filled_qty == Decimal("10")
    assert order.filled_price == Decimal("450.50")
    assert order.is_filled
    assert not order.is_active
    print("  - SUBMITTED -> FILLED transition works")
    
    # Verify pending orders list is empty
    pending = order_machine.get_pending_orders()
    assert len(pending) == 0
    print("  - Pending orders filtering works")
    
    event_bus.stop()
    print("[PASS] Test 2 Complete")
    assert True


def test_3_partial_fills():
    """Test partial fill state handling."""
    print("\n[TEST 3] Partial Fill Handling...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create order for 100 shares
    order = order_machine.create_order(
        order_id="TEST_003",
        symbol="SPY",
        quantity=Decimal("100"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    
    # Submit
    order_machine.transition(
        order_id="TEST_003",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BROKER_456"
    )
    
    # Partial fill: 60 shares
    order_machine.transition(
        order_id="TEST_003",
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.PARTIALLY_FILLED,
        broker_order_id="BROKER_456",
        filled_qty=Decimal("60"),
        remaining_qty=Decimal("40"),
        fill_price=Decimal("450.00")
    )
    
    # Verify partial fill
    order = order_machine.get_order("TEST_003")
    assert order.state == OrderStatus.PARTIALLY_FILLED
    assert order.filled_qty == Decimal("60")
    assert order.remaining_qty == Decimal("40")
    assert order.is_active
    assert order.fill_percentage == Decimal("60")
    print("  - Partial fill state correct")
    
    # Complete fill
    order_machine.transition(
        order_id="TEST_003",
        from_state=OrderStatus.PARTIALLY_FILLED,
        to_state=OrderStatus.FILLED,
        broker_order_id="BROKER_456",
        filled_qty=Decimal("100"),
        fill_price=Decimal("450.00")
    )
    
    # Verify complete fill
    order = order_machine.get_order("TEST_003")
    assert order.state == OrderStatus.FILLED
    assert not order.is_active
    assert order.fill_percentage == Decimal("100")
    print("  - Complete fill state correct")
    
    event_bus.stop()
    print("[PASS] Test 3 Complete")
    assert True


def test_4_order_cancellation():
    """Test order cancellation flow."""
    print("\n[TEST 4] Order Cancellation...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create and submit order
    order = order_machine.create_order(
        order_id="TEST_004",
        symbol="SPY",
        quantity=Decimal("10"),
        side="LONG",
        order_type="LIMIT",
        strategy="test",
        entry_price=Decimal("450.00")
    )
    
    order_machine.transition(
        order_id="TEST_004",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BROKER_789"
    )
    
    # Cancel order
    order_machine.transition(
        order_id="TEST_004",
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.CANCELLED,
        broker_order_id="BROKER_789",
        reason="user_requested"
    )
    
    # Verify cancellation
    order = order_machine.get_order("TEST_004")
    assert order.state == OrderStatus.CANCELLED
    assert order.cancelled_at is not None
    assert not order.is_active
    print("  - Order cancellation works")
    
    # Verify not in pending list
    pending = order_machine.get_pending_orders()
    assert len(pending) == 0
    print("  - Cancelled order excluded from pending")
    
    event_bus.stop()
    print("[PASS] Test 4 Complete")
    assert True


def test_5_order_rejection():
    """Test order rejection flow."""
    print("\n[TEST 5] Order Rejection...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create order
    order = order_machine.create_order(
        order_id="TEST_005",
        symbol="SPY",
        quantity=Decimal("10"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    
    # Reject order (before submission - risk gate rejection)
    order_machine.transition(
        order_id="TEST_005",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.REJECTED,
        reason="insufficient_funds"
    )
    
    # Verify rejection
    order = order_machine.get_order("TEST_005")
    assert order.state == OrderStatus.REJECTED
    assert order.rejection_reason == "insufficient_funds"
    assert not order.is_active
    print("  - Order rejection works")
    
    event_bus.stop()
    print("[PASS] Test 5 Complete")
    assert True


def test_6_pending_orders_filtering():
    """Test pending orders excludes terminal states."""
    print("\n[TEST 6] Pending Orders Filtering...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create multiple orders in different states
    orders_data = [
        ("ORD_PENDING", OrderStatus.PENDING, None),
        ("ORD_SUBMITTED", OrderStatus.SUBMITTED, "BROKER_1"),
        ("ORD_PARTIAL", OrderStatus.PARTIALLY_FILLED, "BROKER_2"),
        ("ORD_FILLED", OrderStatus.FILLED, "BROKER_3"),
        ("ORD_CANCELLED", OrderStatus.CANCELLED, "BROKER_4"),
        ("ORD_REJECTED", OrderStatus.REJECTED, None),
    ]
    
    for order_id, target_state, broker_id in orders_data:
        order = order_machine.create_order(
            order_id=order_id,
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Transition to target state if needed
        if target_state != OrderStatus.PENDING:
            # First submit if needed
            if target_state in [OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED, 
                               OrderStatus.FILLED, OrderStatus.CANCELLED]:
                order_machine.transition(
                    order_id=order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_id
                )
                from_state = OrderStatus.SUBMITTED
            else:
                from_state = OrderStatus.PENDING
            
            # Then transition to final state
            if target_state != OrderStatus.SUBMITTED:
                order_machine.transition(
                    order_id=order_id,
                    from_state=from_state,
                    to_state=target_state,
                    broker_order_id=broker_id,
                    reason="test"
                )
    
    # Get pending orders
    pending = order_machine.get_pending_orders()
    pending_ids = {o.order_id for o in pending}
    
    # Should only have PENDING, SUBMITTED, PARTIALLY_FILLED
    expected = {"ORD_PENDING", "ORD_SUBMITTED", "ORD_PARTIAL"}
    assert pending_ids == expected, f"Expected {expected}, got {pending_ids}"
    print(f"  - Pending orders filtered correctly: {pending_ids}")
    
    event_bus.stop()
    print("[PASS] Test 6 Complete")
    assert True


def test_7_order_properties():
    """Test Order dataclass computed properties."""
    print("\n[TEST 7] Order Properties...")
    
    # Setup
    temp_dir = Path(tempfile.mkdtemp())
    log_path = temp_dir / "test.jsonl"
    
    event_bus = OrderEventBus()
    event_bus.start()
    transaction_log = TransactionLog(log_path)
    order_machine = OrderStateMachine(event_bus, transaction_log)
    
    # Create order
    order = order_machine.create_order(
        order_id="TEST_PROPS",
        symbol="SPY",
        quantity=Decimal("100"),
        side="LONG",
        order_type="MARKET",
        strategy="test"
    )
    
    # Test initial properties
    assert order.fill_percentage == Decimal("0")
    assert order.total_cost is None
    assert order.is_active
    assert order.is_pending
    assert not order.is_filled
    print("  - Initial properties correct")
    
    # Partial fill
    order_machine.transition(
        order_id="TEST_PROPS",
        from_state=OrderStatus.PENDING,
        to_state=OrderStatus.SUBMITTED,
        broker_order_id="BROKER_PROP"
    )
    
    order_machine.transition(
        order_id="TEST_PROPS",
        from_state=OrderStatus.SUBMITTED,
        to_state=OrderStatus.PARTIALLY_FILLED,
        broker_order_id="BROKER_PROP",
        filled_qty=Decimal("60"),
        remaining_qty=Decimal("40"),
        fill_price=Decimal("450.00")
    )
    
    order = order_machine.get_order("TEST_PROPS")
    assert order.fill_percentage == Decimal("60")
    assert order.total_cost == Decimal("27000.00")
    assert order.is_active
    assert not order.is_filled
    print("  - Partial fill properties correct")
    
    event_bus.stop()
    print("[PASS] Test 7 Complete")
    assert True


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("FOCUSED INTEGRATION TESTS - OrderStateMachine Core")
    print("="*70)
    
    tests = [
        test_1_order_creation_and_retrieval,
        test_2_state_transitions,
        test_3_partial_fills,
        test_4_order_cancellation,
        test_5_order_rejection,
        test_6_pending_orders_filtering,
        test_7_order_properties,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"[FAIL] {test_func.__name__}: {e}")
            failed += 1
    
    print("\n" + "="*70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*70)
    
    if failed == 0:
        print("\n[SUCCESS] All core integration tests passed!")
        print("OrderStateMachine is fully functional and ready for use.")
    else:
        print(f"\n[WARNING] {failed} tests failed - review errors above")
    
    sys.exit(0 if failed == 0 else 1)
