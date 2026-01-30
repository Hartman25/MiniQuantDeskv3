"""
Strategy Registry - Factory and validator for strategies.

CRITICAL RESPONSIBILITIES:
1. Register strategy classes
2. Instantiate strategies with config
3. Validate strategy implements IStrategy
4. Prevent duplicate registrations
5. Provide strategy discovery

Ensures all strategies follow contract before use.
"""

from typing import Dict, Type, List, Optional
from decimal import Decimal
import logging

from strategies.base import IStrategy, StrategyMetadata

logger = logging.getLogger(__name__)


# ============================================================================
# STRATEGY REGISTRY
# ============================================================================

class StrategyRegistry:
    """
    Central registry for trading strategies.
    
    INVARIANTS:
    - All strategies MUST inherit from IStrategy
    - Strategy names MUST be unique
    - Strategies are validated on registration
    
    Usage:
        registry = StrategyRegistry()
        
        # Register strategy class
        registry.register(VWAPMeanReversion)
        
        # Create instance
        strategy = registry.create(
            name="vwap_mean_reversion",
            config={"param1": "value1"},
            symbols=["SPY", "QQQ"],
            timeframe="1Min"
        )
    """
    
    def __init__(self):
        self._strategies: Dict[str, Type[IStrategy]] = {}
        self._metadata: Dict[str, StrategyMetadata] = {}
        
        logger.info("StrategyRegistry initialized")
    
    def register(
        self,
        strategy_class: Type[IStrategy],
        metadata: Optional[StrategyMetadata] = None
    ) -> None:
        """
        Register strategy class.
        
        Args:
            strategy_class: Strategy class (must inherit IStrategy)
            metadata: Optional metadata
            
        Raises:
            ValueError: If strategy doesn't inherit IStrategy or name duplicate
        """
        # Validate inheritance
        if not issubclass(strategy_class, IStrategy):
            raise ValueError(
                f"{strategy_class.__name__} must inherit from IStrategy"
            )
        
        # Get strategy name
        name = strategy_class.__name__.lower()
        
        # Check for duplicates
        if name in self._strategies:
            raise ValueError(f"Strategy {name} already registered")
        
        # Register
        self._strategies[name] = strategy_class
        if metadata:
            self._metadata[name] = metadata
        
        logger.info(f"Registered strategy: {name}")
    
    def create(
        self,
        name: str,
        config: Dict,
        symbols: List[str],
        timeframe: str = "1Min"
    ) -> IStrategy:
        """
        Create strategy instance.
        
        Args:
            name: Strategy name (lowercase)
            config: Strategy configuration
            symbols: Symbols to trade
            timeframe: Bar interval
            
        Returns:
            Strategy instance
            
        Raises:
            ValueError: If strategy not registered
        """
        name = name.lower()
        
        strategy_class = self._strategies.get(name)
        if not strategy_class:
            raise ValueError(
                f"Strategy {name} not registered. "
                f"Available: {list(self._strategies.keys())}"
            )
        
        # Instantiate
        strategy = strategy_class(
            name=name,
            config=config,
            symbols=symbols,
            timeframe=timeframe
        )
        
        # Validate
        if not strategy.validate():
            raise ValueError(f"Strategy {name} failed validation")
        
        logger.info(
            f"Created strategy: {name} "
            f"(symbols={symbols}, timeframe={timeframe})"
        )
        
        return strategy
    
    def list_strategies(self) -> List[str]:
        """Get list of registered strategy names."""
        return list(self._strategies.keys())
    
    def get_metadata(self, name: str) -> Optional[StrategyMetadata]:
        """Get strategy metadata."""
        return self._metadata.get(name.lower())
