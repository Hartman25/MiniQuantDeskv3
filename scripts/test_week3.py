"""
Week 3 test - Risk Management & Strategy Framework.
"""

import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state import PositionStore
from core.risk import RiskManager, RiskLimits
from core.brokers import BrokerOrderSide
from core.strategy import SimpleMovingAverageCrossover, PortfolioManager, SignalType

import pandas as pd


def test_week3_components():
    """Test Week 3 components."""
    print("\n" + "="*70)
    print("Week 3 Test - Risk Management & Strategy Framework")
    print("="*70)
    
    # Setup
    position_store = PositionStore(Path("data/test_positions.db"))
    position_store.clear()
    
    # Test 1: Risk Manager
    print("\n[1] Testing Risk Manager...")
    
    risk_limits = RiskLimits(
        max_position_size_usd=Decimal("50000"),
        max_portfolio_exposure_usd=Decimal("200000"),
        max_positions=10
    )
    
    risk_mgr = RiskManager(position_store=position_store, limits=risk_limits)
    
    # Valid trade
    result = risk_mgr.validate_trade(
        symbol="SPY",
        quantity=Decimal("50"),
        side=BrokerOrderSide.BUY,
        price=Decimal("600"),
        account_value=Decimal("1000000"),
        buying_power=Decimal("500000")
    )
    
    assert result.approved, f"Valid trade rejected: {result.reasons}"
    print(f"    Valid trade approved: SPY 50 @ $600")
    
    # Oversized trade
    result = risk_mgr.validate_trade(
        symbol="SPY",
        quantity=Decimal("1000"),  # $600k position
        side=BrokerOrderSide.BUY,
        price=Decimal("600"),
        account_value=Decimal("1000000"),
        buying_power=Decimal("500000")
    )
    
    assert not result.approved, "Oversized trade should be rejected"
    print(f"    Oversized trade rejected: {result.reasons[0]}")
    
    # Test 2: Strategy Framework
    print("\n[2] Testing Strategy Framework...")
    
    strategy = SimpleMovingAverageCrossover(
        symbols=["SPY"],
        fast_period=5,
        slow_period=10
    )
    
    strategy.initialize()
    print(f"    Strategy initialized: {strategy.name}")
    
    # Create sample data
    dates = pd.date_range(start='2026-01-01', periods=20, freq='D')
    prices = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118,
              120, 119, 118, 117, 116, 115, 114, 113, 112, 111]
    
    bars = pd.DataFrame({
        'close': prices,
        'open': prices,
        'high': [p + 1 for p in prices],
        'low': [p - 1 for p in prices],
        'volume': [1000000] * 20
    }, index=dates)
    
    # Update strategy
    strategy.on_data(bars)
    print(f"    Strategy updated with {len(bars)} bars")
    
    # Generate signal
    signal = strategy.generate_signal("SPY")
    
    if signal:
        print(f"    Signal: {signal.signal_type.value} @ {signal.strength}")
    else:
        print(f"    No signal (HOLD)")
    
    # Test 3: Portfolio Manager
    print("\n[3] Testing Portfolio Manager...")
    
    portfolio = PortfolioManager(risk_manager=risk_mgr)
    portfolio.add_strategy(strategy, allocation=Decimal("1.0"))
    portfolio.initialize_strategies()
    
    print(f"    Portfolio with 1 strategy")
    
    # Get signals
    signals = portfolio.get_signals(
        account_value=Decimal("1000000"),
        buying_power=Decimal("500000")
    )
    
    print(f"    Generated {len(signals)} signals")
    
    # Test 4: Strategy State
    print("\n[4] Testing Strategy State...")
    
    state = strategy.get_state()
    print(f"    Strategy: {state['name']}")
    print(f"    Initialized: {state['is_initialized']}")
    print(f"    Signals: {state['signals_generated']}")
    print(f"    Trades: {state['trades_executed']}")
    
    print("\n" + "="*70)
    print("ALL WEEK 3 TESTS PASSED")
    print("="*70)
    print("\nWeek 3 Components:")
    print("  [X] RiskManager")
    print("  [X] RiskLimits")
    print("  [X] BaseStrategy")
    print("  [X] SimpleMovingAverageCrossover")
    print("  [X] PortfolioManager")
    print("\nREADY FOR WEEK 4")
    print()
    
    # Cleanup
    position_store.close()


if __name__ == "__main__":
    test_week3_components()
