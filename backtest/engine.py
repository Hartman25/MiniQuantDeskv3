"""
Main backtest engine.

LEAN COMPATIBILITY:
Based on QuantConnect's BacktestingResultHandler + Algorithm.

ARCHITECTURE:
- Event-driven simulation
- Strategy integration
- Data replay
- Order management
- Performance tracking

Matches live trading interface - strategies run unchanged.
"""

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime
from pathlib import Path

from core.strategy import BaseStrategy, SignalType
from core.brokers import BrokerOrderSide
from backtest.data_handler import HistoricalDataHandler
from backtest.simulated_broker import SimulatedBroker
from backtest.performance import PerformanceAnalyzer, PerformanceMetrics
from backtest.fill_models import (
    FillModel,
    ImmediateFillModel,
    SlippageModel,
    ConstantSlippageModel,
    AssetClass,
    OrderType
)
from backtest.fee_models import FeeModel, AlpacaFeeModel
from core.logging import get_logger, LogStream


# ============================================================================
# BACKTEST ENGINE
# ============================================================================

class BacktestEngine:
    """
    Main backtesting engine.
    
    FEATURES:
    - Event-driven simulation
    - Strategy lifecycle management
    - Realistic fill simulation
    - Commission calculation
    - Performance analytics
    - Multi-symbol support
    
    USAGE:
        engine = BacktestEngine(
            starting_cash=100000,
            data_dir="data/historical",
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 12, 31)
        )
        
        engine.add_strategy(MyStrategy())
        engine.add_symbol("SPY")
        
        results = engine.run()
    """
    
    def __init__(
        self,
        starting_cash: Decimal,
        data_dir: Path,
        start_date: datetime,
        end_date: datetime,
        fill_model: Optional[FillModel] = None,
        slippage_model: Optional[SlippageModel] = None,
        fee_model: Optional[FeeModel] = None,
        asset_class: AssetClass = AssetClass.EQUITY,
        resolution: str = "1Day"
    ):
        """
        Initialize backtest engine.
        
        Args:
            starting_cash: Starting capital
            data_dir: Historical data directory
            start_date: Backtest start date
            end_date: Backtest end date
            fill_model: Fill simulation model
            slippage_model: Slippage model
            fee_model: Commission model
            asset_class: Asset class
            resolution: Bar resolution (1Day, 1Hour, etc)
        """
        self.starting_cash = starting_cash
        self.start_date = start_date
        self.end_date = end_date
        self.asset_class = asset_class
        self.resolution = resolution
        
        # Initialize components
        if not fill_model:
            slippage = slippage_model or ConstantSlippageModel(Decimal("0.0001"))
            fill_model = ImmediateFillModel(slippage_model=slippage)
        
        self.data_handler = HistoricalDataHandler(
            data_dir=Path(data_dir),
            asset_class=asset_class
        )
        
        self.broker = SimulatedBroker(
            starting_cash=starting_cash,
            fill_model=fill_model,
            fee_model=fee_model or AlpacaFeeModel(),
            asset_class=asset_class
        )
        
        self.analyzer = PerformanceAnalyzer(starting_equity=starting_cash)
        
        # Strategy management
        self.strategies: List[BaseStrategy] = []
        self.symbols: List[str] = []
        
        # Current state
        self.current_timestamp: Optional[datetime] = None
        self.current_bars: Dict[str, dict] = {}
        
        self.logger = get_logger(LogStream.SYSTEM)
        
        self.logger.info("BacktestEngine initialized", extra={
            "starting_cash": float(starting_cash),
            "start_date": start_date,
            "end_date": end_date,
            "resolution": resolution
        })
    
    def add_strategy(self, strategy: BaseStrategy):
        """
        Add strategy to backtest.
        
        Args:
            strategy: Strategy instance
        """
        self.strategies.append(strategy)
        self.logger.info(f"Strategy added: {strategy.__class__.__name__}")
    
    def add_symbol(self, symbol: str):
        """
        Add symbol to backtest.
        
        Args:
            symbol: Symbol to trade
        """
        self.symbols.append(symbol)
        
        # Load historical data
        self.data_handler.load_symbol(
            symbol=symbol,
            start_date=self.start_date,
            end_date=self.end_date,
            resolution=self.resolution
        )
        
        self.logger.info(f"Symbol added: {symbol}")
    
    def run(self) -> PerformanceMetrics:
        """
        Run backtest.
        
        Returns:
            Performance metrics
        """
        self.logger.info("Starting backtest...")
        
        # Initialize strategies
        for strategy in self.strategies:
            strategy.initialize()
        
        # Event loop - iterate through historical data
        for timestamp, bars in self.data_handler:
            self.current_timestamp = timestamp
            self.current_bars = bars
            
            # Process fills first (orders from previous bar)
            for symbol in bars.keys():
                filled_orders = self.broker.process_bar(
                    symbol=symbol,
                    bar=bars[symbol],
                    timestamp=timestamp
                )
                
                # Notify strategies of fills
                for order in filled_orders:
                    for strategy in self.strategies:
                        strategy.on_fill(order)
                        
                        # Track trade P&L
                        # Simplified - would need full position tracking
                        # self.analyzer.add_trade(pnl)
            
            # Update strategies with new data
            for strategy in self.strategies:
                strategy.on_data(bars)
            
            # Generate signals
            for strategy in self.strategies:
                for symbol in self.symbols:
                    signal = strategy.generate_signal(symbol)
                    
                    if signal and signal.signal_type != SignalType.HOLD:
                        # Execute signal
                        self._execute_signal(symbol, signal)
            
            # Update portfolio value
            current_prices = {
                symbol: Decimal(str(bars[symbol]['close']))
                for symbol in bars.keys()
            }
            
            portfolio_value = self.broker.get_portfolio_value(current_prices)
            self.analyzer.update(timestamp, portfolio_value)
        
        # Calculate final metrics
        metrics = self.analyzer.get_metrics(
            total_commission=self.broker.total_commission
        )
        
        self.logger.info("Backtest complete", extra={
            "final_equity": float(metrics.final_equity),
            "total_return": float(metrics.total_return),
            "sharpe_ratio": float(metrics.sharpe_ratio),
            "total_trades": metrics.total_trades
        })
        
        return metrics
    
    def _execute_signal(self, symbol: str, signal):
        """Execute trading signal."""
        # Determine order side
        if signal.signal_type == SignalType.LONG:
            side = BrokerOrderSide.BUY
        elif signal.signal_type == SignalType.SHORT:
            side = BrokerOrderSide.SELL
        else:
            return
        
        # Calculate position size
        # Simplified - use fixed quantity or % of portfolio
        current_price = Decimal(str(self.current_bars[symbol]['close']))
        portfolio_value = self.broker.get_portfolio_value({
            s: Decimal(str(self.current_bars[s]['close']))
            for s in self.current_bars.keys()
        })
        
        # Example: 10% of portfolio per position
        position_size_usd = portfolio_value * Decimal("0.10")
        quantity = int(position_size_usd / current_price)
        
        if quantity > 0:
            # Submit order
            order_id = self.broker.submit_order(
                symbol=symbol,
                side=side,
                quantity=Decimal(quantity),
                order_type=OrderType.MARKET
            )
            
            self.logger.debug(f"Signal executed: {signal.signal_type.value}", extra={
                "symbol": symbol,
                "quantity": quantity,
                "order_id": order_id
            })
    
    def get_equity_curve(self) -> List[tuple]:
        """Get equity curve."""
        return self.analyzer.get_equity_curve()
    
    def get_positions(self) -> Dict:
        """Get current positions."""
        return {
            symbol: {
                "quantity": float(pos.quantity),
                "average_cost": float(pos.average_cost)
            }
            for symbol, pos in self.broker.positions.items()
        }
