"""
Patch 3 Tests: Broker Reconciliation Error Handling

Tests verify:
1. AlpacaBrokerConnector.get_orders() method works
2. Reconciler position reconciliation uses correct broker methods  
3. Reconciler order reconciliation works
4. Live mode halts on reconciliation discrepancies
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from decimal import Decimal
from datetime import datetime, timezone

from core.brokers.alpaca_connector import AlpacaBrokerConnector
from core.state.reconciler import BrokerReconciler, Discrepancy
from core.state.position_store import Position
from core.runtime.app import run_app, RunOptions
from pathlib import Path


# ============================================================================
# TEST 1: Broker get_orders() Method
# ============================================================================

def test_broker_get_orders():
    """
    PATCH 3: Verify AlpacaBrokerConnector.get_orders() method exists and works.
    
    This method was added to support reconciliation.
    """
    # Create mock trading client
    mock_client = Mock()
    
    # Mock account for verification
    mock_account = Mock()
    mock_account.account_number = "TEST1234567"
    mock_account.buying_power = "100000.00"
    mock_client.get_account = Mock(return_value=mock_account)
    
    # Mock order objects
    mock_order1 = Mock()
    mock_order1.id = "order_123"
    mock_order1.symbol = "SPY"
    mock_order1.qty = 10
    mock_order1.side = "buy"
    mock_order1.status = "new"
    
    mock_order2 = Mock()
    mock_order2.id = "order_456"
    mock_order2.symbol = "AAPL"
    mock_order2.qty = 5
    mock_order2.side = "sell"
    mock_order2.status = "partially_filled"
    
    mock_client.get_orders = Mock(return_value=[mock_order1, mock_order2])
    
    # Create connector with mocked client
    with patch('core.brokers.alpaca_connector.TradingClient', return_value=mock_client):
        connector = AlpacaBrokerConnector(
            api_key="test_key",
            api_secret="test_secret",
            paper=True
        )
        
        # Call get_orders method
        orders = connector.get_orders(status='open')
        
        # Verify method exists and returns results
        assert len(orders) == 2
        assert orders[0].id == "order_123"
        assert orders[1].id == "order_456"
        
        print("✅ PATCH 3 TEST 1 PASSED: get_orders() method works correctly")


# ============================================================================
# TEST 2: Reconciler Position Reconciliation
# ============================================================================

def test_reconciler_positions():
    """
    PATCH 3: Verify reconciler calls get_positions() (not get_all_positions())
    and correctly handles Position object attributes.
    """
    # Create mocks
    mock_order_machine = Mock()
    mock_position_store = Mock()
    mock_broker = Mock()
    
    # Mock broker positions (Position objects)
    mock_pos = Mock()
    mock_pos.symbol = "SPY"
    mock_pos.quantity = Decimal("10")
    mock_pos.entry_price = Decimal("450.00")
    mock_pos.unrealized_pnl = Decimal("50.00")
    
    # PATCH 3: Broker returns get_positions() (not get_all_positions())
    mock_broker.get_positions = Mock(return_value=[mock_pos])
    mock_broker.get_orders = Mock(return_value=[])
    
    # Mock local positions and orders (both empty)
    mock_position_store.get_all_positions = Mock(return_value=[])
    mock_position_store.open_position = Mock()
    mock_order_machine.get_pending_orders = Mock(return_value=[])  # FIX: Added this
    
    # Create reconciler
    reconciler = BrokerReconciler(
        order_machine=mock_order_machine,
        position_store=mock_position_store,
        broker_connector=mock_broker
    )
    
    # Run reconciliation
    discrepancies = reconciler.reconcile_startup()
    
    # Verify get_positions() was called (PATCH 3 fix)
    mock_broker.get_positions.assert_called_once()
    
    # Verify discrepancy found (missing position locally)
    assert len(discrepancies) == 1
    assert discrepancies[0].type == "missing_position"
    assert discrepancies[0].symbol == "SPY"
    
    # Verify position was added to store
    mock_position_store.open_position.assert_called_once()
    
    print("✅ PATCH 3 TEST 2 PASSED: Position reconciliation uses correct broker methods")


# ============================================================================
# TEST 3: Reconciler Order Reconciliation
# ============================================================================

def test_reconciler_orders():
    """
    PATCH 3: Verify reconciler order reconciliation calls get_orders()
    and handles discrepancies correctly.
    """
    # Create mocks
    mock_order_machine = Mock()
    mock_position_store = Mock()
    mock_broker = Mock()
    
    # Mock broker orders
    mock_order = Mock()
    mock_order.id = "broker_order_123"
    mock_order.symbol = "AAPL"
    mock_order.qty = 5
    mock_order.side = "buy"
    mock_order.status = "new"
    
    mock_broker.get_positions = Mock(return_value=[])
    mock_broker.get_orders = Mock(return_value=[mock_order])
    
    # Mock local positions and orders (empty)
    mock_position_store.get_all_positions = Mock(return_value=[])
    mock_order_machine.get_pending_orders = Mock(return_value=[])
    
    # Create reconciler
    reconciler = BrokerReconciler(
        order_machine=mock_order_machine,
        position_store=mock_position_store,
        broker_connector=mock_broker
    )
    
    # Run reconciliation
    discrepancies = reconciler.reconcile_startup()
    
    # Verify get_orders() was called (PATCH 3 addition)
    mock_broker.get_orders.assert_called_once_with(status='open')
    
    # Verify discrepancy found (missing order locally)
    assert len(discrepancies) == 1
    assert discrepancies[0].type == "missing_order"
    assert discrepancies[0].symbol == "AAPL"
    assert discrepancies[0].resolution == "logged_only"
    
    print("✅ PATCH 3 TEST 3 PASSED: Order reconciliation uses get_orders() correctly")


# ============================================================================
# TEST 4: Live Mode Halt on Reconciliation Discrepancy
# ============================================================================

def test_live_mode_halt_on_discrepancy():
    """
    PATCH 3: Verify live mode stops trading when reconciliation finds discrepancies.
    
    This is a critical safety feature - live mode MUST NOT trade with unknown broker state.
    """
    # Create mock config
    mock_config = MagicMock()
    mock_config.broker.api_key = "test_key"
    mock_config.broker.api_secret = "test_secret"
    mock_config.broker.paper_trading = False  # Live mode
    mock_config.strategies = []
    
    # Create mock container
    mock_container = MagicMock()
    mock_container.get_config = Mock(return_value=mock_config)
    
    # Create mock reconciler with discrepancies
    mock_reconciler = Mock()
    discrepancy = Discrepancy(
        type="missing_position",
        symbol="SPY",
        local_value=None,
        broker_value="10 @ $450",
        resolution="logged_only",
        timestamp=datetime.now(timezone.utc)
    )
    mock_reconciler.reconcile_startup = Mock(return_value=[discrepancy])
    mock_container.get_reconciler = Mock(return_value=mock_reconciler)
    
    # Patch Container class
    with patch('core.runtime.app.Container', return_value=mock_container):
        with patch('core.runtime.app.AlpacaBrokerConnector'):
            with patch('core.runtime.app._ensure_strategy_registry_bootstrapped'):
                # Create run options for LIVE mode
                opts = RunOptions(
                    config_path=Path("config.toml"),
                    mode="live",  # CRITICAL: Live mode
                    run_once=True
                )
                
                # Run app
                exit_code = run_app(opts)
                
                # PATCH 3: Live mode MUST return exit code 1 (failure)
                assert exit_code == 1, "Live mode should halt with exit code 1 on discrepancies"
                
                # Verify container was stopped
                mock_container.stop.assert_called()
                
                print("✅ PATCH 3 TEST 4 PASSED: Live mode halts on reconciliation discrepancies")


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("PATCH 3 TEST SUITE: Broker Reconciliation Error Handling")
    print("=" * 70)
    
    test_broker_get_orders()
    test_reconciler_positions()
    test_reconciler_orders()
    test_live_mode_halt_on_discrepancy()
    
    print("\n" + "=" * 70)
    print("✅ ALL PATCH 3 TESTS PASSED (4/4)")
    print("=" * 70)
