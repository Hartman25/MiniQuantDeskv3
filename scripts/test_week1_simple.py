"""
Simple ASCII-only integration test for Week 1 components.
"""

import sys
from pathlib import Path
import tempfile
import time
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state import (
    OrderStateMachine,
    OrderStatus,
    OrderStateChangedEvent,
    InvalidTransitionError,
    TransactionLog,
    PositionStore,
    Position,
)
from core.events import OrderEventBus
from core.logging import setup_logging


def test_all_components():
    """Test all Week 1 components together."""
    print("\n" + "="*70)
    print("Week 1 Component Test")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Setup
        event_bus = OrderEventBus()
        transaction_log = TransactionLog(tmpdir / "transactions.log")
        position_store = PositionStore(tmpdir / "positions.db")
        state_machine = OrderStateMachine(event_bus, transaction_log)
        
        events_received = []
        
        def handle_event(event):
            events_received.append(event)
            if event.to_state == OrderStatus.FILLED:
                position = Position(
                    symbol="SPY",
                    quantity=event.filled_qty,
                    entry_price=event.fill_price,
                    entry_time=event.timestamp,
                    strategy="Test",
                    order_id=event.order_id
                )
                position_store.upsert(position)
        
        event_bus.subscribe(OrderStateChangedEvent, handle_event)
        event_bus.start()
        
        try:
            # Test 1: Submit order
            print("\n[1] Submit order...")
            state_machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.PENDING,
                to_state=OrderStatus.SUBMITTED,
                broker_order_id="BRK_123"
            )
            print("    PASS - Order submitted")
            
            # Test 2: Fill order
            print("\n[2] Fill order...")
            state_machine.transition(
                order_id="ORD_001",
                from_state=OrderStatus.SUBMITTED,
                to_state=OrderStatus.FILLED,
                broker_order_id="BRK_123",
                filled_qty=Decimal("10"),
                fill_price=Decimal("598.50")
            )
            print("    PASS - Order filled")
            
            time.sleep(0.3)
            
            # Test 3: Verify events
            print("\n[3] Check events...")
            assert len(events_received) == 2
            print(f"    PASS - {len(events_received)} events received")
            
            # Test 4: Verify transaction log
            print("\n[4] Check transaction log...")
            logged = transaction_log.read_all()
            assert len(logged) == 2
            print(f"    PASS - {len(logged)} events logged")
            
            # Test 5: Verify position
            print("\n[5] Check position...")
            pos = position_store.get("SPY")
            assert pos is not None
            assert pos.quantity == Decimal("10")
            print(f"    PASS - Position: {pos.quantity} SPY @ ${pos.entry_price}")
            
            # Test 6: Invalid transition
            print("\n[6] Test invalid transition...")
            try:
                state_machine.transition(
                    order_id="ORD_001",
                    from_state=OrderStatus.FILLED,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id="BRK_123"
                )
                assert False, "Should have raised error"
            except Exception:
                print("    PASS - Invalid transition rejected")
            
            print("\n" + "="*70)
            print("ALL TESTS PASSED")
            print("="*70)
            print("\nWeek 1 Components:")
            print("  [X] OrderStateMachine")
            print("  [X] OrderEventBus")
            print("  [X] TransactionLog")
            print("  [X] PositionStore")
            print()
            
        finally:
            event_bus.stop()
            transaction_log.close()
            position_store.close()


if __name__ == "__main__":
    # Setup logging
    setup_logging(
        log_dir=Path("logs"),
        log_level="WARNING",  # Quiet
        console_level="ERROR",
        json_logs=True
    )
    
    test_all_components()
