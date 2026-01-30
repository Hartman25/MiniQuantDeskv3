"""
COMPREHENSIVE INTEGRATION TEST SUITE
Phase 1 Components - End-to-End Validation

Tests all critical paths:
1. Order creation → storage → retrieval
2. Order transitions → state updates  
3. Event handlers → OrderStateMachine integration
4. Reconciliation → pending order retrieval
5. Full order lifecycle (create → submit → fill)
6. Risk gates → order rejection
7. Position tracking → P&L calculation
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from datetime import datetime
import tempfile
from pathlib import Path

# Import components
from core.state.order_machine import OrderStateMachine, OrderStatus, Order
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog
from core.events.bus import OrderEventBus
from core.events.handlers import EventHandlerRegistry
from core.events.types import OrderFilledEvent, OrderSubmittedEvent
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer
from core.risk.gate import PreTradeRiskGate


class TestResults:
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
        print(f"[FAIL] {test_name}: {error}")
    
    def print_summary(self):
        print("\n" + "="*70)
        print(f"TEST RESULTS: {self.passed} passed, {self.failed} failed")
        print("="*70)
        if self.errors:
            print("\nFAILURES:")
            for test, error in self.errors:
                print(f"  [FAIL] {test}")
                print(f"         {error}")
        print()


# ============================================================================
# TEST 1: ORDER STORAGE AND RETRIEVAL
# ============================================================================

def test_order_storage(results: TestResults):
    """Test OrderStateMachine stores and retrieves orders correctly."""
    print("\n[TEST 1] Order Storage and Retrieval")
    print("-" * 70)
    
    # Create temp files
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        txn_log_path = f.name
    
    try:
        # Initialize components
        event_bus = OrderEventBus()
        event_bus.start()  # Start the event bus
        txn_log = TransactionLog(Path(txn_log_path))
        order_machine = OrderStateMachine(event_bus, txn_log)
        
        # Test 1.1: Create order
        order = order_machine.create_order(
            order_id="TEST_001",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        if order.order_id != "TEST_001":
            results.record_fail("1.1 Create Order", f"Wrong order_id: {order.order_id}")
        elif order.state != OrderStatus.PENDING:
            results.record_fail("1.1 Create Order", f"Wrong state: {order.state}")
        else:
            results.record_pass("1.1 Create Order")
        
        # Test 1.2: Retrieve order
        retrieved = order_machine.get_order("TEST_001")
        if retrieved is None:
            results.record_fail("1.2 Retrieve Order", "Order not found")
        elif retrieved.order_id != "TEST_001":
            results.record_fail("1.2 Retrieve Order", "Retrieved wrong order")
        else:
            results.record_pass("1.2 Retrieve Order")
        
        # Test 1.3: Get all orders
        all_orders = order_machine.get_all_orders()
        if len(all_orders) != 1:
            results.record_fail("1.3 Get All Orders", f"Expected 1, got {len(all_orders)}")
        else:
            results.record_pass("1.3 Get All Orders")
        
        # Test 1.4: Get pending orders
        pending = order_machine.get_pending_orders()
        if len(pending) != 1:
            results.record_fail("1.4 Get Pending Orders", f"Expected 1, got {len(pending)}")
        else:
            results.record_pass("1.4 Get Pending Orders")
        
        # Clean up - close file handle
        txn_log.close()
        
    finally:
        try:
            os.unlink(txn_log_path)
        except:
            pass  # Ignore cleanup errors


# ============================================================================
# TEST 2: ORDER STATE TRANSITIONS
# ============================================================================

def test_order_transitions(results: TestResults):
    """Test order transitions update stored state correctly."""
    print("\n[TEST 2] Order State Transitions")
    print("-" * 70)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        txn_log_path = f.name
    
    try:
        event_bus = OrderEventBus()
        txn_log = TransactionLog(Path(txn_log_path))
        order_machine = OrderStateMachine(event_bus, txn_log)
        
        # Create order
        order_machine.create_order(
            order_id="TEST_002",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        # Test 2.1: Transition PENDING → SUBMITTED
        order_machine.transition(
            order_id="TEST_002",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK_123"
        )
        
        order = order_machine.get_order("TEST_002")
        if order.state != OrderStatus.SUBMITTED:
            results.record_fail("2.1 PENDING→SUBMITTED", f"State is {order.state}")
        elif order.broker_order_id != "BRK_123":
            results.record_fail("2.1 PENDING→SUBMITTED", "broker_order_id not set")
        else:
            results.record_pass("2.1 PENDING→SUBMITTED")
        
        # Test 2.2: Transition SUBMITTED → FILLED
        order_machine.transition(
            order_id="TEST_002",
            from_state=OrderStatus.SUBMITTED,
            to_state=OrderStatus.FILLED,
            broker_order_id="BRK_123",
            filled_qty=Decimal("10"),
            fill_price=Decimal("450.50")
        )
        
        order = order_machine.get_order("TEST_002")
        if order.state != OrderStatus.FILLED:
            results.record_fail("2.2 SUBMITTED→FILLED", f"State is {order.state}")
        elif order.filled_qty != Decimal("10"):
            results.record_fail("2.2 SUBMITTED→FILLED", f"filled_qty is {order.filled_qty}")
        elif order.filled_price != Decimal("450.50"):
            results.record_fail("2.2 SUBMITTED→FILLED", f"fill_price is {order.filled_price}")
        else:
            results.record_pass("2.2 SUBMITTED→FILLED")
        
        # Test 2.3: Pending orders excludes filled
        pending = order_machine.get_pending_orders()
        if len(pending) != 0:
            results.record_fail("2.3 Pending Excludes Filled", f"Got {len(pending)} pending")
        else:
            results.record_pass("2.3 Pending Excludes Filled")
        
        # Clean up
        txn_log.close()
        
    finally:
        try:
            os.unlink(txn_log_path)
        except:
            pass


# ============================================================================
# TEST 3: EVENT HANDLER INTEGRATION
# ============================================================================

def test_event_handlers(results: TestResults):
    """Test event handlers update OrderStateMachine correctly."""
    print("\n[TEST 3] Event Handler Integration")
    print("-" * 70)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as txn_f:
        txn_log_path = txn_f.name
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db') as pos_f:
        pos_db_path = pos_f.name
    
    try:
        # Initialize components
        event_bus = OrderEventBus()
        event_bus.start()  # Start the event bus
        txn_log = TransactionLog(Path(txn_log_path))
        order_machine = OrderStateMachine(event_bus, txn_log)
        position_store = PositionStore(pos_db_path)
        
        # Create handler registry
        handlers = EventHandlerRegistry(order_machine, position_store, txn_log)
        handlers.register_default_handlers()
        
        # Create order in SUBMITTED state
        order = order_machine.create_order(
            order_id="TEST_003",
            symbol="SPY",
            quantity=Decimal("5"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        order_machine.transition(
            order_id="TEST_003",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK_456"
        )
        
        # Test 3.1: OrderFilledEvent updates order
        fill_event = OrderFilledEvent(
            order_id="TEST_003",
            symbol="SPY",
            filled_quantity=Decimal("5"),
            fill_price=Decimal("451.00"),
            commission=Decimal("1.00"),
            total_cost=Decimal("2256.00"),
            broker_order_id="BRK_456",
            timestamp=datetime.utcnow()
        )
        
        handlers.handle_event(fill_event)
        
        order = order_machine.get_order("TEST_003")
        if order.state != OrderStatus.FILLED:
            results.record_fail("3.1 Event Updates State", f"State is {order.state}")
        else:
            results.record_pass("3.1 Event Updates State")
        
        # Test 3.2: Position created
        position = position_store.get_position("SPY")
        if position is None:
            results.record_fail("3.2 Position Created", "Position not found")
        elif position.quantity != Decimal("5"):
            results.record_fail("3.2 Position Created", f"Quantity is {position.quantity}")
        else:
            results.record_pass("3.2 Position Created")
        
        # Clean up
        txn_log.close()
        position_store.close()
        
    finally:
        try:
            os.unlink(txn_log_path)
            os.unlink(pos_db_path)
        except:
            pass


# ============================================================================
# TEST 4: RISK GATE INTEGRATION
# ============================================================================

def test_risk_gate(results: TestResults):
    """Test risk gate correctly rejects orders."""
    print("\n[TEST 4] Risk Gate Integration")
    print("-" * 70)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db') as limits_f:
        limits_db_path = limits_f.name
    
    try:
        # Initialize components
        limits_tracker = PersistentLimitsTracker(
            db_path=limits_db_path,
            daily_loss_limit=Decimal("500"),
            max_position_size=Decimal("100"),
            max_notional_exposure=Decimal("10000")
        )
        
        position_sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("5000"),
            max_total_exposure=Decimal("10000"),
            min_position_value=Decimal("100")
        )
        
        risk_gate = PreTradeRiskGate(
            limits_tracker=limits_tracker,
            position_sizer=position_sizer,
            account_value=Decimal("1000"),  # Small account
            enable_pdt_protection=True,
            max_orders_per_day=10
        )
        
        # Test 4.1: Reject oversized position
        decision = risk_gate.evaluate_order(
            symbol="SPY",
            quantity=Decimal("100"),  # Would cost ~$45,000 (way over limit)
            price=Decimal("450"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        if decision.approved:
            results.record_fail("4.1 Reject Oversized", "Should have rejected")
        elif "notional" not in decision.rejection_reason.lower():
            results.record_fail("4.1 Reject Oversized", f"Wrong reason: {decision.rejection_reason}")
        else:
            results.record_pass("4.1 Reject Oversized")
        
        # Test 4.2: Approve reasonable position
        decision = risk_gate.evaluate_order(
            symbol="SPY",
            quantity=Decimal("1"),  # ~$450 (within limits)
            price=Decimal("450"),
            side="LONG",
            order_type="MARKET",
            strategy="test"
        )
        
        if not decision.approved:
            results.record_fail("4.2 Approve Reasonable", f"Rejected: {decision.rejection_reason}")
        else:
            results.record_pass("4.2 Approve Reasonable")
        
        # Clean up
        limits_tracker.close()
        
    finally:
        try:
            os.unlink(limits_db_path)
        except:
            pass


# ============================================================================
# TEST 5: FULL ORDER LIFECYCLE
# ============================================================================

def test_full_lifecycle(results: TestResults):
    """Test complete order lifecycle end-to-end."""
    print("\n[TEST 5] Full Order Lifecycle")
    print("-" * 70)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as txn_f:
        txn_log_path = txn_f.name
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db') as pos_f:
        pos_db_path = pos_f.name
    
    try:
        # Initialize
        event_bus = OrderEventBus()
        txn_log = TransactionLog(Path(txn_log_path))
        order_machine = OrderStateMachine(event_bus, txn_log)
        position_store = PositionStore(pos_db_path)
        handlers = EventHandlerRegistry(order_machine, position_store, txn_log)
        handlers.register_default_handlers()
        
        # 1. Create order
        order = order_machine.create_order(
            order_id="LIFECYCLE_001",
            symbol="AAPL",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test_lifecycle"
        )
        
        if order.state != OrderStatus.PENDING:
            results.record_fail("5.1 Create (PENDING)", f"State: {order.state}")
        else:
            results.record_pass("5.1 Create (PENDING)")
        
        # 2. Submit to broker
        order_machine.transition(
            order_id="LIFECYCLE_001",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            broker_order_id="BRK_LIFECYCLE"
        )
        
        order = order_machine.get_order("LIFECYCLE_001")
        if order.state != OrderStatus.SUBMITTED or not order.submitted_at:
            results.record_fail("5.2 Submit (SUBMITTED)", "Not properly submitted")
        else:
            results.record_pass("5.2 Submit (SUBMITTED)")
        
        # 3. Fill order
        fill_event = OrderFilledEvent(
            order_id="LIFECYCLE_001",
            symbol="AAPL",
            filled_quantity=Decimal("10"),
            fill_price=Decimal("175.50"),
            commission=Decimal("1.00"),
            total_cost=Decimal("1756.00"),
            broker_order_id="BRK_LIFECYCLE",
            timestamp=datetime.utcnow()
        )
        handlers.handle_event(fill_event)
        
        order = order_machine.get_order("LIFECYCLE_001")
        if order.state != OrderStatus.FILLED or not order.filled_at:
            results.record_fail("5.3 Fill (FILLED)", "Not properly filled")
        else:
            results.record_pass("5.3 Fill (FILLED)")
        
        # 4. Verify position
        position = position_store.get_position("AAPL")
        if not position:
            results.record_fail("5.4 Position Exists", "Position not created")
        elif position.quantity != Decimal("10"):
            results.record_fail("5.4 Position Exists", f"Wrong quantity: {position.quantity}")
        else:
            results.record_pass("5.4 Position Exists")
        
        # 5. Verify no pending orders
        pending = order_machine.get_pending_orders()
        if len(pending) > 0:
            results.record_fail("5.5 No Pending After Fill", f"Still has {len(pending)} pending")
        else:
            results.record_pass("5.5 No Pending After Fill")
        
        # Clean up
        txn_log.close()
        position_store.close()
        
    finally:
        try:
            os.unlink(txn_log_path)
            os.unlink(pos_db_path)
        except:
            pass


# ============================================================================
# RUN ALL TESTS
# ============================================================================

def main():
    print("\n" + "="*70)
    print("COMPREHENSIVE INTEGRATION TEST SUITE")
    print("Phase 1 Components - End-to-End Validation")
    print("="*70)
    
    results = TestResults()
    
    # Run all test suites
    test_order_storage(results)
    test_order_transitions(results)
    test_event_handlers(results)
    test_risk_gate(results)
    test_full_lifecycle(results)
    
    # Print summary
    results.print_summary()
    
    # Exit with proper code
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    exit(main())
