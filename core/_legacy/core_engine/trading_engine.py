"""
Main trading engine - coordinates all components.
"""

from typing import List
from pathlib import Path

from core.logging import get_logger, LogStream, setup_logging
from core.config import load_config
from core.state import OrderStateMachine, TransactionLog, PositionStore
from core.events import OrderEventBus
from core.brokers import AlpacaBrokerConnector
from core.data import MarketDataPipeline
from core.execution import OrderExecutionEngine, PositionReconciliation
from core.risk import RiskManager
from core.strategies.base import BaseStrategy
from core.portfolio import PortfolioManager
from core.realtime.datafeed import RealtimeDataFeed


class TradingEngine:
    """
    Main trading engine.
    
    RESPONSIBILITIES:
    - Initialize all components
    - Coordinate data flow
    - Manage strategy lifecycle
    - Handle shutdown
    """
    
    def __init__(self, config_dir: Path):
        """Initialize engine."""
        self.config = load_config(config_dir)
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Core components
        self.event_bus = OrderEventBus()
        self.transaction_log = TransactionLog(Path("data/transactions.log"))
        self.position_store = PositionStore(Path("data/positions.db"))
        self.state_machine = OrderStateMachine(self.event_bus, self.transaction_log)
        
        # Broker
        self.broker = AlpacaBrokerConnector(
            api_key=self.config.broker.api_key,
            api_secret=self.config.broker.api_secret,
            paper=self.config.broker.paper_trading
        )
        
        # Data
        self.data_pipeline = MarketDataPipeline(
            alpaca_api_key=self.config.broker.api_key,
            alpaca_api_secret=self.config.broker.api_secret
        )
        
        self.realtime_feed = RealtimeDataFeed(
            api_key=self.config.broker.api_key,
            api_secret=self.config.broker.api_secret,
            paper=self.config.broker.paper_trading
        )
        
        # Execution
        self.execution = OrderExecutionEngine(
            broker=self.broker,
            state_machine=self.state_machine,
            position_store=self.position_store
        )
        
        self.reconciliation = PositionReconciliation(
            broker=self.broker,
            position_store=self.position_store
        )
        
        # Risk
        self.risk_manager = RiskManager(position_store=self.position_store)
        
        # Portfolio
        self.portfolio = PortfolioManager(
            execution_engine=self.execution,
            risk_manager=self.risk_manager,
            position_store=self.position_store,
            broker_connector=self.broker
        )
        
        # Strategies
        self.strategies: List[BaseStrategy] = []
        
        self.logger.info("TradingEngine initialized")
    
    def add_strategy(self, strategy: BaseStrategy):
        """Add trading strategy."""
        self.strategies.append(strategy)
        self.logger.info(f"Strategy added: {strategy.name}")
    
    def start(self):
        """Start engine."""
        self.logger.info("Starting trading engine...")
        
        # Start event bus
        self.event_bus.start()
        
        # Reconcile positions
        result = self.reconciliation.reconcile()
        if result.has_drift:
            self.logger.warning("Position drift detected on startup")
        
        # Subscribe to real-time data
        all_symbols = []
        for strategy in self.strategies:
            all_symbols.extend(strategy.symbols)
        
        if all_symbols:
            self.realtime_feed.subscribe_bars(list(set(all_symbols)))
            self.realtime_feed.add_bar_handler(self._on_bar)
            self.realtime_feed.start()
        
        self.logger.info("Trading engine started")
    
    def stop(self):
        """Stop engine."""
        self.logger.info("Stopping trading engine...")
        
        # Stop real-time feed
        self.realtime_feed.stop()
        
        # Stop event bus
        self.event_bus.stop()
        
        # Close resources
        self.transaction_log.close()
        self.position_store.close()
        
        self.logger.info("Trading engine stopped")
    
    def _on_bar(self, bar: dict):
        """Handle real-time bar."""
        symbol = bar["symbol"]
        
        # Get historical data for strategy
        try:
            bars = self.data_pipeline.get_latest_bars(symbol, lookback_bars=100)
            
            # Run strategies
            for strategy in self.strategies:
                if symbol in strategy.symbols:
                    signal = strategy.on_bar(symbol, bars)
                    
                    if signal:
                        self.logger.info(f"Signal: {signal.symbol} {signal.direction.value}")
                        self.portfolio.evaluate_signal(signal)
                        
        except Exception as e:
            self.logger.error(f"Error processing bar for {symbol}: {e}", exc_info=True)
