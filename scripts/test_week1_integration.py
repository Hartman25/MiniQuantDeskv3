"""
Integration test for Week 1 core components.

Tests:
1. OrderStateMachine - Valid/invalid transitions
2. OrderEventBus - Event distribution
3. TransactionLog - Persistence
4. PositionStore - CRUD operations
5. Full integration - Complete order lifecycle
"""

import sys
from pathlib import Path
import tempfile
import time
from decimal import Decimal
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state import (
    OrderStateMachine,
    OrderStatus,
    OrderStateChangedEvent,
    InvalidTransitionError,
    BrokerConfirmationRequiredError,
    TerminalStateError,
    TransactionLog,
    PositionStore,
    Position,
)
from core.events import OrderEventBus
from core.logging import setup_logging


def test_order_state_machine():
    """Test OrderStateMachine transitions."""
    print("\n" + "="*60)
    print("TEST 1: OrderStateMachine")
    print("="*60)
    
    # Create temporary components
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        event_bus = OrderEventBus()
        event_bus.start()
        
        transaction_log = TransactionLog(tmpdir / "transactions.log")
        machine = OrderStateMachine(event_bus, transaction_log)
        
        try:
            # Test 1.1: Valid transition PENDING -> SUBMITTED
            print("\n[TEST 1.1] Valid transition: PENDING -> SUBMITTED")
            event = machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK_123"
            )
            assert event.to_state == OrderStatus.SUBMITTED
            print(f"[PASS] Order transitioned to {event.to_state.value}")
            
            # Test 1.2: Valid transition SUBMITTED → FILLED
            print("\n[TEST 1.2] Valid transition: SUBMITTED → FILLED")
            event = machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.FILLED,
                broker_order_id="BRK_123",
                filled_qty=Decimal("10"),
                fill_price=Decimal("100.50")
            )
            assert event.to_state == OrderStatus.FILLED
            assert event.filled_qty == Decimal("10")
            print(f"[PASS] Order filled: {event.filled_qty} @ ${event.fill_price}")
            
            # Test 1.3: Invalid transition (terminal state)
            print("\n[TEST 1.3] Invalid transition: FILLED → SUBMITTED (terminal)")
            try:
                machine.transition(
                    order_id="ORD_001",
                    from_state=OrderStatus.FILLED,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id="BRK_123"
                )
                assert False, "Should have raised TerminalStateError"
            except TerminalStateError as e:
                print(f"[PASS] Correctly rejected: {e}")
            
            # Test 1.4: Invalid transition (no path)
            print("\n[TEST 1.4] Invalid transition: PENDING → FILLED (no path)")
            try:
                machine.transition(
                    order_id="ORD_002",
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.FILLED,
                    broker_order_id="BRK_124"
                )
                assert False, "Should have raised InvalidTransitionError"
            except InvalidTransitionError as e:
                print(f"[PASS] Correctly rejected: {e}")
            
            # Test 1.5: Missing broker confirmation
            print("\n[TEST 1.5] Missing broker confirmation")
            try:
                machine.transition(
                    order_id="ORD_003",
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED
                    # No broker_order_id
                )
                assert False, "Should have raised BrokerConfirmationRequiredError"
            except BrokerConfirmationRequiredError as e:
                print(f"[PASS] Correctly rejected: {e}")
            
            # Test 1.6: Partial fill flow
            print("\n[TEST 1.6] Partial fill flow")
            event = machine.transition(
                order_id="ORD_004",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK_125"
            )
            event = machine.transition(
                order_id="ORD_004",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.PARTIALLY_FILLED,
                broker_order_id="BRK_125",
                filled_qty=Decimal("5"),
                remaining_qty=Decimal("5"),
                fill_price=Decimal("99.75")
            )
            assert event.to_state == OrderStatus.PARTIALLY_FILLED
            event = machine.transition(
                order_id="ORD_004",
                from_state=OrderStatus.PARTIALLY_FILLED,
                to_state=OrderStatus.FILLED,
                broker_order_id="BRK_125",
                filled_qty=Decimal("10"),
                fill_price=Decimal("100.00")
            )
            assert event.to_state == OrderStatus.FILLED
            print(f"[PASS] Partial fill completed")
            
            print("\n[PASS] All OrderStateMachine tests passed")
            
        finally:
            event_bus.stop()
            transaction_log.close()


def test_transaction_log():
    """Test TransactionLog persistence."""
    print("\n" + "="*60)
    print("TEST 2: TransactionLog")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        log_path = tmpdir / "transactions.log"
        
        # Write events
        print("\n[TEST 2.1] Write events")
        with TransactionLog(log_path) as log:
            event1 = OrderStateChangedEvent(
                order_id="ORD_001",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                timestamp=datetime.utcnow(),
                broker_order_id="BRK_123"
            )
            log.append(event1)
            
            event2 = OrderStateChangedEvent(
                order_id="ORD_001",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.FILLED,
                timestamp=datetime.utcnow(),
                broker_order_id="BRK_123",
                filled_qty=Decimal("10"),
                fill_price=Decimal("100.50")
            )
            log.append(event2)
        
        print(f"[PASS] Wrote 2 events")
        
        # Read events back
        print("\n[TEST 2.2] Read events")
        with TransactionLog(log_path) as log:
            events = log.read_all()
            assert len(events) == 2
            assert events[0]['order_id'] == "ORD_001"
            assert events[0]['to_state'] == "SUBMITTED"
            assert events[1]['to_state'] == "FILLED"
            print(f"[PASS] Read {len(events)} events correctly")
        
        print("\n[PASS] All TransactionLog tests passed")


def test_position_store():
    """Test PositionStore CRUD."""
    print("\n" + "="*60)
    print("TEST 3: PositionStore")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "positions.db"
        
        with PositionStore(db_path) as store:
            # Test 3.1: Insert position
            print("\n[TEST 3.1] Insert position")
            pos = Position(
                symbol="SPY",
                quantity=Decimal("10.5"),  # Fractional
                entry_price=Decimal("598.75"),
                entry_time=datetime.utcnow(),
                strategy="VWAPMeanReversion",
                order_id="ORD_001",
                stop_loss=Decimal("595.00"),
                take_profit=Decimal("602.00")
            )
            store.upsert(pos)
            print(f"[PASS] Inserted position: {pos.symbol}")
            
            # Test 3.2: Retrieve position
            print("\n[TEST 3.2] Retrieve position")
            retrieved = store.get("SPY")
            assert retrieved is not None
            assert retrieved.symbol == "SPY"
            assert retrieved.quantity == Decimal("10.5")
            assert retrieved.entry_price == Decimal("598.75")
            print(f"[PASS] Retrieved: {retrieved.quantity} {retrieved.symbol} @ ${retrieved.entry_price}")
            
            # Test 3.3: Update position
            print("\n[TEST 3.3] Update position")
            pos.current_price = Decimal("600.00")
            pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.quantity
            store.upsert(pos)
            
            updated = store.get("SPY")
            assert updated.current_price == Decimal("600.00")
            print(f"[PASS] Updated current price: ${updated.current_price}")
            
            # Test 3.4: Get all positions
            print("\n[TEST 3.4] Get all positions")
            all_positions = store.get_all()
            assert len(all_positions) == 1
            print(f"[PASS] Found {len(all_positions)} positions")
            
            # Test 3.5: Delete position
            print("\n[TEST 3.5] Delete position")
            deleted = store.delete("SPY")
            assert deleted is True
            
            retrieved = store.get("SPY")
            assert retrieved is None
            print(f"[PASS] Position deleted")
            
        print("\n[PASS] All PositionStore tests passed")


def test_event_bus():
    """Test OrderEventBus."""
    print("\n" + "="*60)
    print("TEST 4: OrderEventBus")
    print("="*60)
    
    events_received = []
    
    def handler(event):
        events_received.append(event)
    
    # Test 4.1: Subscribe and emit
    print("\n[TEST 4.1] Subscribe and emit")
    bus = OrderEventBus()
    bus.subscribe(OrderStateChangedEvent, handler)
    bus.start()
    
    try:
        event = OrderStateChangedEvent(
            order_id="ORD_001",
            from_state=OrderStatus.PENDING,
            to_state=OrderStatus.SUBMITTED,
            timestamp=datetime.utcnow(),
            broker_order_id="BRK_123"
        )
        bus.emit(event)
        
        # Wait for processing
        time.sleep(0.2)
        
        assert len(events_received) == 1
        assert events_received[0].order_id == "ORD_001"
        print(f"[PASS] Event received by handler")
        
        # Test 4.2: Multiple events
        print("\n[TEST 4.2] Multiple events")
        for i in range(5):
            event = OrderStateChangedEvent(
                order_id=f"ORD_{i:03d}",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                timestamp=datetime.utcnow(),
                broker_order_id=f"BRK_{i:03d}"
            )
            bus.emit(event)
        
        time.sleep(0.3)
        assert len(events_received) == 6  # 1 + 5
        print(f"[PASS] All events processed")
        
        # Test 4.3: Statistics
        print("\n[TEST 4.3] Statistics")
        stats = bus.get_stats()
        print(f"Events processed: {stats['events_processed']}")
        print(f"Events failed: {stats['events_failed']}")
        assert stats['events_processed'] == 6
        print(f"[PASS] Statistics correct")
        
    finally:
        bus.stop()
    
    print("\n[PASS] All OrderEventBus tests passed")


def test_full_integration():
    """Test all components together."""
    print("\n" + "="*60)
    print("TEST 5: Full Integration")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Setup components
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(tmpdir / "transactions.log")
        position_store = PositionStore(tmpdir / "positions.db")
        state_machine = OrderStateMachine(event_bus, transaction_log)
        
        # Track events
        events_received = []
        
        def handle_state_change(event):
            events_received.append(event)
            
            # On fill, create position
            if event.to_state == OrderStatus.FILLED:
                position = Position(
                    symbol="SPY",
                    quantity=event.filled_qty,
                    entry_price=event.fill_price,
                    entry_time=event.timestamp,
                    strategy="TestStrategy",
                    order_id=event.order_id
                )
                position_store.upsert(position)
        
        event_bus.subscribe(OrderStateChangedEvent, handle_state_change)
        event_bus.start()
        
        try:
            print("\n[TEST 5.1] Complete order lifecycle")
            
            # Submit order
            state_machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK_123"
            )
            
            # Fill order
            state_machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.FILLED,
                broker_order_id="BRK_123",
                filled_qty=Decimal("10"),
                fill_price=Decimal("598.50")
            )
            
            # Wait for event processing
            time.sleep(0.2)
            
            # Verify events received
            assert len(events_received) == 2
            print(f"[PASS] Received {len(events_received)} events")
            
            # Verify transaction log
            logged_events = transaction_log.read_all()
            assert len(logged_events) == 2
            print(f"[PASS] Transaction log contains {len(logged_events)} events")
            
            # Verify position created
            position = position_store.get("SPY")
            assert position is not None
            assert position.quantity == Decimal("10")
            assert position.entry_price == Decimal("598.50")
            print(f"[PASS] Position created: {position.quantity} {position.symbol} @ ${position.entry_price}")
            
            print("\n[PASS] Full integration test passed")
            
        finally:
            event_bus.stop()
            transaction_log.close()
            position_store.close()


def main():
    """Run all integration tests."""
    print("\n" + "="*70)
    print(" MiniQuantDesk v2 - Week 1 Component Integration Test")
    print("="*70)
    
    # Setup logging
    setup_logging(
        log_dir=Path("logs"),
        log_level="INFO",
        console_level="WARNING",
        json_logs=True
    )
    
    # Run tests
    test_order_state_machine()
    test_transaction_log()
    test_position_store()
    test_event_bus()
    test_full_integration()
    
    print("\n" + "="*70)
    print(" ALL TESTS PASSED ✓")
    print("="*70)
    print("\nWeek 1 core components are fully operational:")
    print("  ✓ OrderStateMachine - State transitions with guards")
    print("  ✓ TransactionLog - Append-only event persistence")
    print("  ✓ PositionStore - SQLite-backed CRUD")
    print("  ✓ OrderEventBus - Thread-safe event distribution")
    print("  ✓ Full Integration - All components working together")
    print("\nNext: Week 2 - Broker connectors and data pipeline")
    print()


if __name__ == "__main__":
    main()
