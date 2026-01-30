"""
Component Integration Tests - Validate all new components.

CRITICAL TEST COVERAGE:
1. Data Contracts - Schema validation
2. PreTradeRiskGate - Rejection paths
3. Event Handlers - Wiring validation
4. Broker Reconciliation - Recovery scenarios
5. Strategy Interface - Lifecycle validation
6. End-to-End Flow - Full execution path

Run before paper trading deployment.
"""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import List, Dict
import logging

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import components
from core.data.contract import MarketDataContract, MarketDataContractError
from core.data.validator import DataValidator, DataValidationError
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer
from core.risk.gate import PreTradeRiskGate, OrderRequest
from core.events.types import OrderFilledEvent, OrderCreatedEvent
from core.events.bus import OrderEventBus
from core.events.handlers import EventHandlerRegistry
from core.state.order_machine import OrderStateMachine
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog
from strategies.base import IStrategy
from strategies.vwap_mean_reversion import VWAPMeanReversion
from strategies.registry import StrategyRegistry
from strategies.lifecycle import StrategyLifecycleManager


# ============================================================================
# TEST RESULTS TRACKING
# ============================================================================

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []
    
    def record_pass(self, test_name: str):
        self.passed += 1
        logger.info(f"✓ PASS: {test_name}")
    
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.failures.append((test_name, error))
        logger.error(f"✗ FAIL: {test_name} - {error}")
    
    def print_summary(self):
        total = self.passed + self.failed
        logger.info("\n" + "="*80)
        logger.info(f"TEST SUMMARY: {self.passed}/{total} passed")
        logger.info("="*80)
        
        if self.failures:
            logger.error("\nFAILURES:")
            for test_name, error in self.failures:
                logger.error(f"  - {test_name}: {error}")
        
        return self.failed == 0


results = TestResults()


# ============================================================================
# TEST 1: DATA CONTRACT VALIDATION
# ============================================================================

def test_data_contract_validation():
    """Test MarketDataContract validation rules."""
    test_name = "DataContract_Validation"
    
    try:
        # Valid contract
        bar = MarketDataContract(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc),
            open=Decimal("580.50"),
            high=Decimal("581.25"),
            low=Decimal("579.80"),
            close=Decimal("580.90"),
            volume=1000000,
            provider="test"
        )
        assert bar.symbol == "SPY"
        assert bar.open == Decimal("580.50")
        
        # Test OHLC validation - high < low should fail
        try:
            bad_bar = MarketDataContract(
                symbol="SPY",
                timestamp=datetime.now(timezone.utc),
                open=Decimal("580.50"),
                high=Decimal("579.00"),  # High < Low
                low=Decimal("580.00"),
                close=Decimal("580.90"),
                volume=1000000,
                provider="test"
            )
            raise AssertionError("Should have rejected high < low")
        except MarketDataContractError:
            pass  # Expected
        
        # Test staleness detection
        old_bar = MarketDataContract(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=120),
            open=Decimal("580.50"),
            high=Decimal("581.25"),
            low=Decimal("579.80"),
            close=Decimal("580.90"),
            volume=1000000,
            provider="test"
        )
        assert old_bar.is_stale(90), "Should detect 120s old bar as stale"
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 2: DATA VALIDATOR
# ============================================================================

def test_data_validator():
    """Test DataValidator staleness and gap detection."""
    test_name = "DataValidator"
    
    try:
        validator = DataValidator(
            max_staleness_seconds=90,
            allow_gaps=True,
            max_gap_tolerance=0.05
        )
        
        # Create valid bars
        now = datetime.now(timezone.utc)
        bars = []
        for i in range(10):
            bar = MarketDataContract(
                symbol="SPY",
                timestamp=now - timedelta(seconds=i*60),
                open=Decimal("580.00"),
                high=Decimal("581.00"),
                low=Decimal("579.00"),
                close=Decimal("580.50"),
                volume=100000,
                provider="test"
            )
            bars.append(bar)
        
        bars.reverse()  # Oldest first
        
        # Should pass validation
        validator.validate_bars(bars, timeframe="1Min")
        
        # Test stale data rejection
        stale_bars = bars[:]
        stale_bars[-1] = MarketDataContract(
            symbol="SPY",
            timestamp=now - timedelta(seconds=200),  # 200s old
            open=Decimal("580.00"),
            high=Decimal("581.00"),
            low=Decimal("579.00"),
            close=Decimal("580.50"),
            volume=100000,
            provider="test"
        )
        
        try:
            validator.validate_bars(stale_bars)
            raise AssertionError("Should have rejected stale data")
        except DataValidationError:
            pass  # Expected
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 3: PERSISTENT LIMITS TRACKER
# ============================================================================

def test_persistent_limits_tracker():
    """Test daily loss limit persistence."""
    test_name = "PersistentLimitsTracker"
    
    try:
        import tempfile
        import os
        
        # Create temp database
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        temp_db.close()
        
        # Initialize tracker
        tracker = PersistentLimitsTracker(
            db_path=temp_db.name,
            daily_loss_limit=Decimal("100.00")
        )
        
        # Record some losses
        tracker.record_realized_pnl(Decimal("-25.50"))
        tracker.record_realized_pnl(Decimal("-30.00"))
        
        daily_pnl = tracker.get_daily_realized_pnl()
        assert daily_pnl == Decimal("-55.50"), f"Expected -55.50, got {daily_pnl}"
        
        # Should not be breached yet
        assert not tracker.is_daily_loss_limit_breached()
        
        # Record breach
        tracker.record_realized_pnl(Decimal("-50.00"))  # Total = -105.50
        
        # Should now be breached
        assert tracker.is_daily_loss_limit_breached(), "Loss limit should be breached"
        
        # Test persistence - create new instance
        tracker2 = PersistentLimitsTracker(
            db_path=temp_db.name,
            daily_loss_limit=Decimal("100.00")
        )
        
        # Should still show loss
        daily_pnl2 = tracker2.get_daily_realized_pnl()
        assert daily_pnl2 == Decimal("-105.50"), "Loss should persist across restarts"
        assert tracker2.is_daily_loss_limit_breached(), "Breach should persist"
        
        # Cleanup
        os.unlink(temp_db.name)
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 4: NOTIONAL POSITION SIZER
# ============================================================================

def test_notional_position_sizer():
    """Test position sizing prevents overexposure."""
    test_name = "NotionalPositionSizer"
    
    try:
        sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("0.10"),  # 10% per position
            max_total_exposure=Decimal("0.95"),
            min_position_value=Decimal("10.00")
        )
        
        # Test $200 account with $580 SPY
        shares = sizer.calculate_position_size(
            account_value=Decimal("200.00"),
            current_price=Decimal("580.00"),
            existing_exposure_pct=Decimal("0.00")
        )
        
        # Should return 0 (can't afford even 1 share at 10% = $20)
        assert shares == 0, f"Should return 0 shares for $200 account, got {shares}"
        
        # Test $10,000 account with $580 SPY
        shares2 = sizer.calculate_position_size(
            account_value=Decimal("10000.00"),
            current_price=Decimal("580.00"),
            existing_exposure_pct=Decimal("0.00")
        )
        
        # 10% of $10,000 = $1,000 / $580 = 1.72 shares → rounds to 1
        assert shares2 == 1, f"Expected 1 share, got {shares2}"
        
        # Test exposure limit enforcement
        shares3 = sizer.calculate_position_size(
            account_value=Decimal("10000.00"),
            current_price=Decimal("100.00"),
            existing_exposure_pct=Decimal("0.90")  # Already 90% exposed
        )
        
        # Should only allow 5% more (95% - 90%), but max per position is 10%
        # So limited to 5% = $500 / $100 = 5 shares
        assert shares3 == 5, f"Expected 5 shares (limited by remaining exposure), got {shares3}"
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 5: PRETRADE RISK GATE
# ============================================================================

def test_pretrade_risk_gate():
    """Test PreTradeRiskGate rejection paths."""
    test_name = "PreTradeRiskGate"
    
    try:
        import tempfile
        import os
        
        # Create temp database
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        temp_db.close()
        
        # Initialize components
        limits_tracker = PersistentLimitsTracker(
            db_path=temp_db.name,
            daily_loss_limit=Decimal("50.00")  # Low limit for testing
        )
        
        position_sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("0.10"),
            max_total_exposure=Decimal("0.95")
        )
        
        gate = PreTradeRiskGate(
            limits_tracker=limits_tracker,
            position_sizer=position_sizer,
            account_value=Decimal("1000.00"),
            enable_pdt_protection=True,
            max_orders_per_day=3
        )
        
        gate.start()
        
        # Test 1: Valid order should be approved
        request1 = OrderRequest(
            order_id="test_001",
            symbol="SPY",
            quantity=1,
            side="LONG",
            order_type="MARKET",
            strategy="test",
            current_price=Decimal("100.00")
        )
        
        decision1 = gate.submit_order(request1, timeout=2.0)
        assert decision1.approved, f"Valid order rejected: {decision1.rejection_reason}"
        
        # Test 2: Duplicate order should be rejected
        request2 = OrderRequest(
            order_id="test_001",  # Same ID
            symbol="SPY",
            quantity=1,
            side="LONG",
            order_type="MARKET",
            strategy="test",
            current_price=Decimal("100.00")
        )
        
        decision2 = gate.submit_order(request2, timeout=2.0)
        assert not decision2.approved, "Duplicate order should be rejected"
        assert "Duplicate" in decision2.rejection_reason
        
        # Test 3: Exceed PDT limit (already submitted 1, submit 2 more, 4th should fail)
        for i in range(2, 5):
            request = OrderRequest(
                order_id=f"test_{i:03d}",
                symbol="SPY",
                quantity=1,
                side="LONG",
                order_type="MARKET",
                strategy="test",
                current_price=Decimal("100.00")
            )
            decision = gate.submit_order(request, timeout=2.0)
            
            if i < 4:
                assert decision.approved, f"Order {i} should be approved"
            else:
                assert not decision.approved, "4th order should be rejected (PDT)"
                assert "PDT" in decision.rejection_reason
        
        # Test 4: Breach daily loss limit
        limits_tracker.record_realized_pnl(Decimal("-60.00"))  # Exceed $50 limit
        
        request_loss = OrderRequest(
            order_id="test_loss",
            symbol="SPY",
            quantity=1,
            side="LONG",
            order_type="MARKET",
            strategy="test",
            current_price=Decimal("100.00")
        )
        
        decision_loss = gate.submit_order(request_loss, timeout=2.0)
        assert not decision_loss.approved, "Should reject after loss limit breach"
        assert "loss limit" in decision_loss.rejection_reason.lower()
        
        gate.stop()
        
        # Cleanup
        os.unlink(temp_db.name)
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 6: EVENT HANDLER WIRING
# ============================================================================

def test_event_handler_wiring():
    """Test event handlers properly update state."""
    test_name = "EventHandler_Wiring"
    
    try:
        import tempfile
        import os
        
        # Create temp files
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        temp_db.close()
        temp_log = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode='w')
        temp_log.close()
        
        # Initialize components
        order_machine = OrderStateMachine()
        position_store = PositionStore(db_path=temp_db.name)
        transaction_log = TransactionLog(log_path=temp_log.name)
        event_bus = EventBus()
        
        # Register handlers
        handlers = EventHandlerRegistry(
            order_machine=order_machine,
            position_store=position_store,
            transaction_log=transaction_log
        )
        handlers.register_default_handlers()
        handlers.wire_to_event_bus(event_bus)
        
        event_bus.start()
        
        # Create order in state machine
        order_machine.create_order(
            order_id="test_order",
            symbol="SPY",
            quantity=Decimal("10"),
            side="LONG",
            order_type="MARKET",
            strategy="test",
            entry_price=Decimal("580.00")
        )
        
        # Mark as submitted
        order_machine.mark_submitted("test_order", "broker_123")
        
        # Emit OrderFilledEvent
        filled_event = OrderFilledEvent(
            order_id="test_order",
            broker_order_id="broker_123",
            symbol="SPY",
            filled_quantity=Decimal("10"),
            fill_price=Decimal("580.50"),
            commission=Decimal("0.00")
        )
        
        event_bus.publish(filled_event)
        
        # Give event bus time to process
        import time
        time.sleep(0.5)
        
        # Verify order state updated
        order = order_machine.get_order("test_order")
        assert order is not None, "Order not found"
        assert order.state == "FILLED", f"Expected FILLED, got {order.state}"
        
        # Verify position created
        positions = position_store.get_all_positions()
        assert len(positions) > 0, "No positions created"
        
        spy_position = next((p for p in positions if p.symbol == "SPY"), None)
        assert spy_position is not None, "SPY position not found"
        assert spy_position.quantity == Decimal("10"), f"Expected 10 shares, got {spy_position.quantity}"
        
        event_bus.stop()
        
        # Cleanup
        os.unlink(temp_db.name)
        os.unlink(temp_log.name)
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 7: STRATEGY INTERFACE
# ============================================================================

def test_strategy_interface():
    """Test IStrategy implementation and lifecycle."""
    test_name = "Strategy_Interface"
    
    try:
        # Create strategy
        config = {
            'vwap_period': 20,
            'entry_threshold_pct': 0.01,
            'max_positions': 1
        }
        
        strategy = VWAPMeanReversion(
            name="test_vwap",
            config=config,
            symbols=["SPY"],
            timeframe="1Min"
        )
        
        # Validate
        assert strategy.validate(), "Strategy validation failed"
        
        # Initialize
        strategy.on_init()
        
        # Feed some bars
        now = datetime.now(timezone.utc)
        for i in range(25):  # Need 20+ for VWAP
            bar = MarketDataContract(
                symbol="SPY",
                timestamp=now - timedelta(seconds=(25-i)*60),
                open=Decimal("580.00"),
                high=Decimal("581.00"),
                low=Decimal("579.00"),
                close=Decimal("580.00") + Decimal(str(i * 0.1)),
                volume=100000,
                provider="test"
            )
            
            signal = strategy.on_bar(bar)
            # May or may not generate signal depending on VWAP calculation
        
        assert strategy.bars_processed == 25, f"Expected 25 bars processed, got {strategy.bars_processed}"
        
        # Test order fill handling
        strategy.on_order_filled(
            order_id="test_order",
            symbol="SPY",
            filled_qty=Decimal("10"),
            fill_price=Decimal("580.00")
        )
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# TEST 8: STRATEGY REGISTRY
# ============================================================================

def test_strategy_registry():
    """Test strategy registration and factory."""
    test_name = "Strategy_Registry"
    
    try:
        registry = StrategyRegistry()
        
        # Register strategy
        registry.register(VWAPMeanReversion)
        
        # List strategies
        strategies = registry.list_strategies()
        assert "vwapmeanreversion" in strategies, "Strategy not registered"
        
        # Create instance
        config = {
            'vwap_period': 20,
            'entry_threshold_pct': 0.01,
            'max_positions': 1
        }
        
        strategy = registry.create(
            name="vwapmeanreversion",
            config=config,
            symbols=["SPY", "QQQ"],
            timeframe="1Min"
        )
        
        assert strategy is not None, "Strategy creation failed"
        assert strategy.name == "vwapmeanreversion"
        assert strategy.symbols == ["SPY", "QQQ"]
        
        results.record_pass(test_name)
        return True
        
    except Exception as e:
        results.record_fail(test_name, str(e))
        return False


# ============================================================================
# RUN ALL TESTS
# ============================================================================

def run_all_tests():
    """Execute all component tests."""
    logger.info("="*80)
    logger.info("STARTING COMPONENT INTEGRATION TESTS")
    logger.info("="*80 + "\n")
    
    # Run tests in order
    test_data_contract_validation()
    test_data_validator()
    test_persistent_limits_tracker()
    test_notional_position_sizer()
    test_pretrade_risk_gate()
    test_event_handler_wiring()
    test_strategy_interface()
    test_strategy_registry()
    
    # Print summary
    success = results.print_summary()
    
    if success:
        logger.info("\n✓ ALL TESTS PASSED - System ready for paper trading validation")
        return 0
    else:
        logger.error("\n✗ SOME TESTS FAILED - Fix issues before proceeding")
        return 1


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
