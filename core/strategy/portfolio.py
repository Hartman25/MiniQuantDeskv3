"""
Portfolio manager - coordinates multiple strategies.

RESPONSIBILITIES:
- Strategy registration and lifecycle
- Signal aggregation from multiple strategies
- Position allocation across strategies
- Trade routing and execution coordination

Based on multi-strategy portfolio management.
"""

from typing import List, Dict, Optional
from decimal import Decimal
from datetime import datetime

from core.logging import get_logger, LogStream
from core.strategy.base import BaseStrategy, TradingSignal, SignalType
from core.risk.manager import RiskManager, RiskCheckResult
from core.brokers import BrokerOrderSide


# ============================================================================
# PORTFOLIO MANAGER
# ============================================================================

class PortfolioManager:
    """
    Multi-strategy portfolio manager.
    
    ARCHITECTURE:
    - Manages multiple strategies
    - Aggregates signals
    - Enforces risk limits
    - Routes trades
    
    USAGE:
        portfolio = PortfolioManager(risk_manager=risk_mgr)
        
        # Register strategies
        portfolio.add_strategy(strategy1)
        portfolio.add_strategy(strategy2)
        
        # Initialize all
        portfolio.initialize_strategies()
        
        # Process data
        portfolio.update_strategies(bars)
        
        # Get signals
        signals = portfolio.get_signals()
    """
    
    def __init__(self, risk_manager: RiskManager):
        """Initialize portfolio manager."""
        self.risk_manager = risk_manager
        self.logger = get_logger(LogStream.PORTFOLIO)
        
        # Strategies
        self._strategies: Dict[str, BaseStrategy] = {}
        
        # Allocation (strategy -> weight)
        self._allocations: Dict[str, Decimal] = {}
        
        self.logger.info("PortfolioManager initialized")
    
    def add_strategy(self, strategy: BaseStrategy, allocation: Decimal = Decimal("1.0")):
        """
        Add strategy to portfolio.
        
        Args:
            strategy: Strategy instance
            allocation: Weight (0.0 to 1.0)
        """
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy {strategy.name} already exists")
        
        self._strategies[strategy.name] = strategy
        self._allocations[strategy.name] = allocation
        
        self.logger.info(f"Strategy added: {strategy.name}", extra={
            "strategy": strategy.name,
            "allocation": str(allocation)
        })
    
    def initialize_strategies(self):
        """Initialize all strategies."""
        for name, strategy in self._strategies.items():
            if not strategy.is_initialized:
                strategy.initialize()
                self.logger.info(f"Strategy initialized: {name}")
    
    def update_strategies(self, bars_by_symbol: Dict[str, 'pd.DataFrame']):
        """
        Update all strategies with new data.
        
        Args:
            bars_by_symbol: Dict of symbol -> DataFrame
        """
        for strategy in self._strategies.values():
            for symbol in strategy.symbols:
                if symbol in bars_by_symbol:
                    strategy.on_data(bars_by_symbol[symbol])
    
    def get_signals(self, account_value: Decimal, buying_power: Decimal) -> List[TradingSignal]:
        """
        Get validated signals from all strategies.
        
        Args:
            account_value: Current account value
            buying_power: Current buying power
            
        Returns:
            List of validated signals
        """
        all_signals = []
        
        # Collect signals from each strategy
        for strategy in self._strategies.values():
            for symbol in strategy.symbols:
                signal = strategy.generate_signal(symbol)
                
                if signal and signal.signal_type != SignalType.HOLD:
                    all_signals.append(signal)
        
        # Deduplicate by symbol (prioritize strongest signal)
        signals_by_symbol = {}
        for signal in all_signals:
            if signal.symbol not in signals_by_symbol:
                signals_by_symbol[signal.symbol] = signal
            else:
                # Keep strongest
                if signal.strength > signals_by_symbol[signal.symbol].strength:
                    signals_by_symbol[signal.symbol] = signal
        
        # Validate against risk limits
        validated = []
        for signal in signals_by_symbol.values():
            if self._validate_signal(signal, account_value, buying_power):
                validated.append(signal)
        
        self.logger.info(f"Generated {len(validated)} validated signals", extra={
            "total_signals": len(all_signals),
            "validated": len(validated)
        })
        
        return validated
    
    def _validate_signal(
        self,
        signal: TradingSignal,
        account_value: Decimal,
        buying_power: Decimal
    ) -> bool:
        """Validate signal against risk limits."""
        # Convert signal to trade parameters
        # This is simplified - real version would calculate position size
        quantity = Decimal("10")  # Placeholder
        price = Decimal("600")    # Placeholder - would get from market data
        
        side = BrokerOrderSide.BUY if signal.signal_type == SignalType.LONG else BrokerOrderSide.SELL
        
        result = self.risk_manager.validate_trade(
            symbol=signal.symbol,
            quantity=quantity,
            side=side,
            price=price,
            account_value=account_value,
            buying_power=buying_power,
            strategy=signal.strategy
        )
        
        if not result.approved:
            self.logger.warning(f"Signal rejected by risk: {signal.symbol}", extra={
                "signal": signal.signal_type.value,
                "reasons": result.reasons
            })
        
        return result.approved
    
    def get_strategy_states(self) -> Dict[str, Dict]:
        """Get state of all strategies."""
        return {
            name: strategy.get_state()
            for name, strategy in self._strategies.items()
        }
