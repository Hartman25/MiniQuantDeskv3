"""
COMPREHENSIVE INTEGRATION TESTS
Phase 1 Component Integration Validation

Tests the complete flow:
1. Order creation and storage
2. State transitions
3. Event handler updates
4. Position updates
5. Reconciliation
6. End-to-end execution

Run before paper trading deployment.
"""

import sys
sys.path.insert(0, r'C:\Users\Zacha\Desktop\MiniQuantDeskv2')

from decimal import Decimal
from datetime import datetime, timezone
import tempfile
import os
from pathlib import Path

# Components under test
from core.state.order_machine import OrderStateMachine, OrderStatus, Order
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog
from core.events.bus import OrderEventBus
from core.events.handlers import EventHandlerRegistry
from core.events.types import OrderFilledEvent, OrderPartiallyFilledEvent, OrderCancelledEvent
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer
from core.risk.gate import PreTradeRiskGate


class TestResults:
    """Track test results."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def record_pass(self, test_name: str):
        self.passed += 1
        print(f"[PASS] {test_name}")
    
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"[FAIL] {test_name}")
        print(f"   Error: {error}")
    
    def summary(self):
        print(f"\n{'='*70}")
        print(f"TEST SUMMARY: {self.passed} passed, {self.failed} failed")
        print(f"{'='*70}")
        
        if self.errors:
            print("\nFAILURES:")
            for test_name, error in self.errors:
                print(f"\n{test_name}:")
                print(f"  {error}")
        
        return self.failed == 0


def test_1_order_storage_and_retrieval(results: TestResults):
    """Test OrderStateMachine stores and retrieves orders."""
    test_name = "Order Storage and Retrieval"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
        order_machine = OrderStateMachine(event_bus, transaction_log)
        
        # Create order
        order = order_machine.create_order(
            order_id="TEST_001",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test_strategy"
        )
        
        # Retrieve order
        retrieved = order_machine.get_order("TEST_001")
        
        # Assertions
        assert retrieved is not None, "Order not found after creation"
        assert retrieved.order_id == "TEST_001", "Order ID mismatch"
        assert retrieved.symbol == "SPY", "Symbol mismatch"
        assert retrieved.quantity == Decimal("10"), "Quantity mismatch"
        assert retrieved.state == OrderStatus.PENDING, "Initial state should be PENDING"
        assert retrieved.is_active, "Order should be active"
        assert retrieved.is_pending, "Order should be pending"
        
        # Get all orders
        all_orders = order_machine.get_all_orders()
        assert len(all_orders) == 1, "Should have exactly 1 order"
        
        # Get pending orders
        pending = order_machine.get_pending_orders()
        assert len(pending) == 1, "Should have 1 pending order"
        
        # Cleanup
        os.unlink(log_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_2_order_state_transitions(results: TestResults):
    """Test state transitions update stored orders."""
    test_name = "Order State Transitions"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
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
        
        # Transition to SUBMITTED
        order_machine.transition(
            order_id="TEST_002",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BROKER_123"
        )
        
        # Verify update
        order = order_machine.get_order("TEST_002")
        assert order.state == OrderStatus.SUBMITTED, "State should be SUBMITTED"
        assert order.broker_order_id == "BROKER_123", "Broker ID should be set"
        assert order.submitted_at is not None, "Submitted timestamp should be set"
        
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
        assert order.state == OrderStatus.FILLED, "State should be FILLED"
        assert order.filled_qty == Decimal("10"), "Filled qty should be set"
        assert order.filled_price == Decimal("450.50"), "Fill price should be set"
        assert order.filled_at is not None, "Filled timestamp should be set"
        assert order.is_filled, "Order should report as filled"
        assert not order.is_active, "Filled order should not be active"
        
        # Verify pending orders list is empty
        pending = order_machine.get_pending_orders()
        assert len(pending) == 0, "Should have no pending orders after fill"
        
        # Cleanup
        os.unlink(log_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_3_event_handler_integration(results: TestResults):
    """Test event handlers update OrderStateMachine."""
    test_name = "Event Handler Integration"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
            db_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
        order_machine = OrderStateMachine(event_bus, transaction_log)
        position_store = PositionStore(db_path)
        
        # Create event handler registry
        handlers = EventHandlerRegistry(
            order_machine=order_machine,
            position_store=position_store,
            transaction_log=transaction_log
        )
        handlers.register_default_handlers()
        
        # Create order
        order = order_machine.create_order(
            order_id="TEST_003",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Transition to SUBMITTED first
        order_machine.transition(
            order_id="TEST_003",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BROKER_456"
        )
        
        # Emit OrderFilledEvent
        fill_event = OrderFilledEvent(
            order_id="TEST_003",
            symbol="SPY",
            filled_quantity=Decimal("10"),
            fill_price=Decimal("450.00"),
            commission=Decimal("1.00"),
            total_cost=Decimal("4501.00"),
            broker_order_id="BROKER_456",
            timestamp=datetime.now(timezone.utc)
        )
        
        # Handle event
        handlers.handle_event(fill_event)
        
        # Verify order was updated
        order = order_machine.get_order("TEST_003")
        assert order.state == OrderStatus.FILLED, "Order should be FILLED after event"
        assert order.filled_qty == Decimal("10"), "Filled qty should be updated"
        assert order.filled_price == Decimal("450.00"), "Fill price should be updated"
        
        # Verify position was created
        position = position_store.get_position("SPY")
        assert position is not None, "Position should be created"
        assert position.quantity == Decimal("10"), "Position quantity should match"
        
        # Cleanup
        os.unlink(log_path)
        os.unlink(db_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_4_partial_fill_handling(results: TestResults):
    """Test partial fill events update correctly."""
    test_name = "Partial Fill Handling"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
            db_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
        order_machine = OrderStateMachine(event_bus, transaction_log)
        position_store = PositionStore(db_path)
        
        handlers = EventHandlerRegistry(order_machine, position_store, transaction_log)
        handlers.register_default_handlers()
        
        # Create order for 100 shares
        order = order_machine.create_order(
            order_id="TEST_004",
            symbol="SPY",
            quantity=Decimal("100"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Submit
        order_machine.transition(
            order_id="TEST_004",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BROKER_789"
        )
        
        # Partial fill: 60 shares
        partial_event = OrderPartiallyFilledEvent(
            order_id="TEST_004",
            symbol="SPY",
            filled_quantity=Decimal("60"),
            remaining_quantity=Decimal("40"),
            fill_price=Decimal("450.00"),
            broker_order_id="BROKER_789",
            timestamp=datetime.now(timezone.utc)
        )
        handlers.handle_event(partial_event)
        
        # Verify partial fill
        order = order_machine.get_order("TEST_004")
        assert order.state == OrderStatus.PARTIALLY_FILLED, "Should be PARTIALLY_FILLED"
        assert order.filled_qty == Decimal("60"), "Should show 60 filled"
        assert order.remaining_qty == Decimal("40"), "Should show 40 remaining"
        assert order.is_active, "Partially filled order is still active"
        
        # Verify position created with partial qty
        position = position_store.get_position("SPY")
        assert position is not None, "Position should exist"
        assert position.quantity == Decimal("60"), "Position should have partial quantity"
        
        # Complete fill: remaining 40 shares
        fill_event = OrderFilledEvent(
            order_id="TEST_004",
            symbol="SPY",
            filled_quantity=Decimal("100"),  # Total filled
            fill_price=Decimal("450.00"),
            commission=Decimal("1.00"),
            total_cost=Decimal("45001.00"),
            broker_order_id="BROKER_789",
            timestamp=datetime.now(timezone.utc)
        )
        handlers.handle_event(fill_event)
        
        # Verify complete fill
        order = order_machine.get_order("TEST_004")
        assert order.state == OrderStatus.FILLED, "Should be FILLED"
        assert not order.is_active, "Filled order is not active"
        
        # Cleanup
        os.unlink(log_path)
        os.unlink(db_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_5_order_cancellation(results: TestResults):
    """Test order cancellation flow."""
    test_name = "Order Cancellation"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
            db_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
        order_machine = OrderStateMachine(event_bus, transaction_log)
        position_store = PositionStore(db_path)
        
        handlers = EventHandlerRegistry(order_machine, position_store, transaction_log)
        handlers.register_default_handlers()
        
        # Create and submit order
        order = order_machine.create_order(
            order_id="TEST_005",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="LIMIT",
            strategy="test",
            entry_price=Decimal("450.00")
        )
        
        order_machine.transition(
            order_id="TEST_005",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BROKER_999"
        )
        
        # Cancel order
        cancel_event = OrderCancelledEvent(
            order_id="TEST_005",
            symbol="SPY",
            reason="user_requested",
            broker_order_id="BROKER_999",
            timestamp=datetime.now(timezone.utc)
        )
        handlers.handle_event(cancel_event)
        
        # Verify cancellation
        order = order_machine.get_order("TEST_005")
        assert order.state == OrderStatus.CANCELLED, "Should be CANCELLED"
        assert order.cancelled_at is not None, "Cancelled timestamp should be set"
        assert not order.is_active, "Cancelled order is not active"
        
        # Verify no position created
        position = position_store.get_position("SPY")
        assert position is None, "No position should exist for cancelled order"
        
        # Cleanup
        os.unlink(log_path)
        os.unlink(db_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_6_risk_gate_integration(results: TestResults):
    """Test risk gate with order storage."""
    test_name = "Risk Gate Integration"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
            limits_db = f.name
        
        limits_tracker = PersistentLimitsTracker(
            db_path=limits_db,
            daily_loss_limit=Decimal("500.00"),
            max_position_size=Decimal("1000.00"),
            max_notional_exposure=Decimal("50000.00")
        )
        
        position_sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("0.20"),  # 20% per position
            max_total_exposure=Decimal("0.95"),          # 95% total
            min_position_value=Decimal("100.00")
        )
        
        risk_gate = PreTradeRiskGate(
            limits_tracker=limits_tracker,
            position_sizer=position_sizer,
            account_value=Decimal("10000.00"),
            enable_pdt_protection=True,
            max_orders_per_day=100
        )
        
        # Test valid order
        signal = {
            'symbol': 'SPY',
            'side': 'LONG',
            'quantity': Decimal("10"),
            'price': Decimal("450.00"),
            'order_type': 'MARKET',
            'strategy': 'test'
        }
        
        approved, rejection_reason = risk_gate.pre_trade_check(signal)
        assert approved, f"Valid order should be approved, got: {rejection_reason}"
        
        # Test oversized order (would exceed position limit)
        large_signal = signal.copy()
        large_signal['quantity'] = Decimal("1000")  # $450,000 position on $10k account
        
        approved, rejection_reason = risk_gate.pre_trade_check(large_signal)
        assert not approved, "Oversized order should be rejected"
        assert rejection_reason is not None, "Should have rejection reason"
        
        # Cleanup
        os.unlink(limits_db)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_7_pending_orders_filtering(results: TestResults):
    """Test pending orders filtering excludes terminal states."""
    test_name = "Pending Orders Filtering"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
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
            
            # Transition to target state
            if target_state != OrderStatus.PENDING:
                order_machine.transition(
                    order_id=order_id,
                    from_state=OrderStatus.PENDING,
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
        
        # Cleanup
        os.unlink(log_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


def test_8_order_properties(results: TestResults):
    """Test Order dataclass computed properties."""
    test_name = "Order Properties"
    
    try:
        # Setup
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            log_path = f.name
        
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(Path(log_path))
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
        assert order.fill_percentage == Decimal("0"), "Initial fill % should be 0"
        assert order.total_cost is None, "No cost before fill"
        assert order.is_active, "Should be active"
        assert order.is_pending, "Should be pending"
        assert not order.is_filled, "Should not be filled"
        
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
        assert order.fill_percentage == Decimal("60"), "Should be 60% filled"
        assert order.total_cost == Decimal("27000.00"), "Cost = 60 * 450"
        assert order.is_active, "Partially filled is still active"
        assert not order.is_filled, "Not fully filled yet"
        
        # Cleanup
        os.unlink(log_path)
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("COMPREHENSIVE INTEGRATION TESTS - Phase 1")
    print("="*70)
    print()
    
    results = TestResults()
    
    # Run tests
    test_1_order_storage_and_retrieval(results)
    test_2_order_state_transitions(results)
    test_3_event_handler_integration(results)
    test_4_partial_fill_handling(results)
    test_5_order_cancellation(results)
    test_6_risk_gate_integration(results)
    test_7_pending_orders_filtering(results)
    test_8_order_properties(results)
    
    # Summary
    success = results.summary()
    
    if success:
        print("\n[SUCCESS] ALL TESTS PASSED - System ready for paper trading validation!")
    else:
        print("\n[WARNING] SOME TESTS FAILED - Fix issues before proceeding")
    
    sys.exit(0 if success else 1)
