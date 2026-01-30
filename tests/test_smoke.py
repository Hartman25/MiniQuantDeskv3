"""
Smoke Tests - Verify basic runability of MiniQuantDesk v2.

These tests ensure:
1. Imports work
2. Config loads
3. Container initializes
4. Key components can be instantiated
5. Data validation works (anti-lookahead)
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# Test imports work
def test_imports():
    """Verify all core imports work."""
    from core.runtime.app import run_app, RunOptions
    from core.di.container import Container
    from core.data.contract import MarketDataContract
    from core.data.validator import DataValidator
    from strategies.vwap_mean_reversion import VWAPMeanReversion
    assert True


def test_config_loads():
    """Verify config file can be loaded."""
    from core.config.loader import ConfigLoader
    
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        pytest.skip("Config file not found")
    
    # FIXED: ConfigLoader.load() takes no arguments, uses config_dir from __init__
    loader = ConfigLoader(config_dir=config_path.parent)
    cfg = loader.load()
    
    assert cfg is not None
    assert 'risk' in cfg
    assert 'broker' in cfg
    assert 'strategies' in cfg


def test_container_initialization():
    """Verify DI container can initialize."""
    from core.di.container import Container
    
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        pytest.skip("Config file not found")
    
    container = Container()
    container.initialize(str(config_path))
    
    # Check key components can be retrieved
    assert container.get_config() is not None
    assert container.get_strategy_registry() is not None
    assert container.get_data_validator() is not None


def test_market_data_contract_validation():
    """Verify MarketDataContract validates correctly."""
    from core.data.contract import MarketDataContract, MarketDataContractError
    
    # Valid bar
    bar = MarketDataContract(
        symbol="SPY",
        timestamp=datetime.now(timezone.utc),
        open=Decimal("580.00"),
        high=Decimal("581.00"),
        low=Decimal("579.00"),
        close=Decimal("580.50"),
        volume=1000000,
        provider="test"
    )
    assert bar.symbol == "SPY"
    
    # Invalid: high < low
    with pytest.raises(MarketDataContractError):
        MarketDataContract(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc),
            open=Decimal("580.00"),
            high=Decimal("579.00"),  # WRONG: high < low
            low=Decimal("581.00"),
            close=Decimal("580.50"),
            volume=1000000,
            provider="test"
        )
    
    # Invalid: negative price
    with pytest.raises(MarketDataContractError):
        MarketDataContract(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc),
            open=Decimal("-580.00"),  # WRONG: negative
            high=Decimal("581.00"),
            low=Decimal("579.00"),
            close=Decimal("580.50"),
            volume=1000000,
            provider="test"
        )


def test_bar_completion_check():
    """Verify is_complete() correctly identifies incomplete bars (anti-lookahead)."""
    from core.data.contract import MarketDataContract
    
    now = datetime.now(timezone.utc)
    
    # Create a 1-minute bar that started 30 seconds ago
    bar_time = now - timedelta(seconds=30)
    bar = MarketDataContract(
        symbol="SPY",
        timestamp=bar_time,
        open=Decimal("580.00"),
        high=Decimal("581.00"),
        low=Decimal("579.00"),
        close=Decimal("580.50"),
        volume=1000000,
        provider="test"
    )
    
    # Bar should NOT be complete (still has 30 seconds left)
    assert not bar.is_complete(timeframe="1Min", reference_time=now)
    
    # Create a 1-minute bar that started 70 seconds ago
    old_bar_time = now - timedelta(seconds=70)
    old_bar = MarketDataContract(
        symbol="SPY",
        timestamp=old_bar_time,
        open=Decimal("580.00"),
        high=Decimal("581.00"),
        low=Decimal("579.00"),
        close=Decimal("580.50"),
        volume=1000000,
        provider="test"
    )
    
    # Bar SHOULD be complete (closed 10 seconds ago)
    assert old_bar.is_complete(timeframe="1Min", reference_time=now)


def test_data_validator_rejects_incomplete_bars():
    """Verify DataValidator rejects incomplete bars."""
    from core.data.contract import MarketDataContract
    from core.data.validator import DataValidator, DataValidationError
    
    now = datetime.now(timezone.utc)
    validator = DataValidator(require_complete_bars=True)
    
    # Create incomplete bar (30 seconds old)
    incomplete_bar = MarketDataContract(
        symbol="SPY",
        timestamp=now - timedelta(seconds=30),
        open=Decimal("580.00"),
        high=Decimal("581.00"),
        low=Decimal("579.00"),
        close=Decimal("580.50"),
        volume=1000000,
        provider="test"
    )
    
    # Should raise error for incomplete bar
    with pytest.raises(DataValidationError) as exc_info:
        validator.validate_single_bar(incomplete_bar, timeframe="1Min")
    
    assert "INCOMPLETE BAR REJECTED" in str(exc_info.value)


def test_strategy_registration():
    """Verify strategies can be registered."""
    from strategies.registry import StrategyRegistry
    from strategies.vwap_mean_reversion import VWAPMeanReversion
    
    registry = StrategyRegistry()
    registry.register(VWAPMeanReversion)
    
    # FIXED: Registry lowercases strategy names
    # VWAPMeanReversion → vwapmeanreversion
    assert "vwapmeanreversion" in registry.list_strategies()
    
    # Verify can create instance (also uses lowercase name)
    strategy = registry.create(
        name="vwapmeanreversion",  # FIXED: Use lowercase
        config={"vwap_period": 20, "entry_threshold_pct": 0.02, "max_positions": 1},
        symbols=["SPY"],
        timeframe="1Min"
    )
    assert strategy is not None
    # FIXED: strategy.name is lowercase (registry passes lowercase to constructor)
    assert strategy.name == "vwapmeanreversion"


def test_run_options():
    """Verify RunOptions can be created."""
    from core.runtime.app import RunOptions
    from pathlib import Path
    
    opts = RunOptions(
        config_path=Path("config/config.yaml"),
        mode="paper",
        run_once=True
    )
    
    assert opts.mode == "paper"
    assert opts.run_once == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
