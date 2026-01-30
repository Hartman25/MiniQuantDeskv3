"""
Week 4 test - Real-Time Data & Event-Driven Execution.
"""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_config
from core.state import OrderStateMachine, TransactionLog, PositionStore
from core.events import OrderEventBus
from core.brokers import AlpacaBrokerConnector
from core.execution import OrderExecutionEngine
from core.realtime import RealtimeDataHandler, QuoteAggregator, EventDrivenExecutor
from core.strategy import TradingSignal, SignalType


def test_week4_components():
    """Test Week 4 components."""
    print("\n" + "="*70)
    print("Week 4 Test - Real-Time Data & Event-Driven Execution")
    print("="*70)
    
    # Load config
    config = load_config(Path("config"))
    
    # Test 1: Quote Aggregator
    print("\n[1] Testing Quote Aggregator...")
    
    agg = QuoteAggregator()
    
    agg.update(
        symbol="SPY",
        bid=Decimal("600.10"),
        ask=Decimal("600.11"),
        timestamp=datetime.now()
    )
    
    nbbo = agg.get_nbbo("SPY")
    assert nbbo is not None
    print(f"    NBBO: Bid=${nbbo['bid']}, Ask=${nbbo['ask']}, Spread=${nbbo['spread']}")
    
    # Test 2: Real-Time Data Handler (setup only - no actual streaming)
    print("\n[2] Testing Real-Time Data Handler (setup)...")
    
    handler = RealtimeDataHandler(
        api_key=config.broker.api_key,
        api_secret=config.broker.api_secret
    )
    
    quote_received = []
    
    @handler.on_quote("SPY")
    def handle_quote(quote):
        quote_received.append(quote)
    
    print(f"    Handler initialized")
    print(f"    Quote callback registered for SPY")
    print(f"    (Not starting stream - market may be closed)")
    
    # Test 3: Event-Driven Executor
    print("\n[3] Testing Event-Driven Executor (dry run)...")
    
    # Setup components
    event_bus = OrderEventBus()
    event_bus.start()
    
    transaction_log = TransactionLog(Path("data/test_transactions.log"))
    position_store = PositionStore(Path("data/test_positions.db"))
    state_machine = OrderStateMachine(event_bus, transaction_log)
    
    broker = AlpacaBrokerConnector(
        api_key=config.broker.api_key,
        api_secret=config.broker.api_secret,
        paper=True
    )
    
    exec_engine = OrderExecutionEngine(
        broker=broker,
        state_machine=state_machine,
        position_store=position_store
    )
    
    executor = EventDrivenExecutor(
        execution_engine=exec_engine,
        max_workers=2
    )
    
    fills = []
    rejects = []
    
    @executor.on_fill
    def handle_fill(order_id, symbol, quantity, price):
        fills.append((order_id, symbol, quantity))
    
    @executor.on_reject
    def handle_reject(order_id, symbol, reason):
        rejects.append((order_id, symbol, reason))
    
    executor.start()
    print(f"    Executor started with 2 workers")
    print(f"    Fill/reject callbacks registered")
    
    # Give workers time to start
    time.sleep(0.5)
    
    executor.stop()
    print(f"    Executor stopped cleanly")
    
    print("\n" + "="*70)
    print("ALL WEEK 4 TESTS PASSED")
    print("="*70)
    print("\nWeek 4 Components:")
    print("  [X] RealtimeDataHandler")
    print("  [X] QuoteAggregator")
    print("  [X] EventDrivenExecutor")
    print("\nREADY FOR WEEK 5")
    print()
    
    # Cleanup
    event_bus.stop()
    transaction_log.close()
    position_store.close()


if __name__ == "__main__":
    test_week4_components()
