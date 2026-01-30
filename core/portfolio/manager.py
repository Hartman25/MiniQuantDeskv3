"""
Portfolio manager - evaluates signals and executes trades.
"""

from typing import List, Optional
from decimal import Decimal
from datetime import datetime

from core.logging import get_logger, LogStream
from core.time import Clock
from core.strategies.base import Signal, SignalDirection
from core.risk.manager import RiskManager
from core.execution import OrderExecutionEngine
from core.brokers import BrokerOrderSide
from core.state import PositionStore, Position


class PortfolioManager:
    """
    Portfolio manager.
    
    RESPONSIBILITIES:
    - Receive signals from strategies
    - Check risk limits
    - Execute approved trades
    - Track positions
    """
    
    def __init__(
        self,
        execution_engine: OrderExecutionEngine,
        risk_manager: RiskManager,
        position_store: PositionStore,
        broker_connector,
        clock: Clock
    ):
        self.execution = execution_engine
        self.risk = risk_manager
        self.positions = position_store
        self.broker = broker_connector
        self.clock = clock  # NEW: Injectable clock for backtesting
        self.logger = get_logger(LogStream.TRADING)
        
        self._order_counter = 0
        
        self.logger.info("PortfolioManager initialized")
    
    def evaluate_signal(self, signal: Signal) -> bool:
        """
        Evaluate signal and execute if approved.
        
        Returns:
            True if executed, False if blocked
        """
        self.logger.info(f"Evaluating signal: {signal.symbol} {signal.direction.value}")
        
        # Check if we already have position
        existing = self.positions.get(signal.symbol)
        
        # LONG entry
        if signal.direction == SignalDirection.LONG:
            if existing:
                self.logger.info(f"Already long {signal.symbol}, skipping")
                return False
            
            return self._execute_long_entry(signal)
        
        # LONG exit
        elif signal.direction == SignalDirection.CLOSE_LONG:
            if not existing or existing.quantity <= 0:
                self.logger.info(f"No long position in {signal.symbol}, skipping")
                return False
            
            return self._execute_long_exit(signal, existing)
        
        # SHORT not implemented yet
        else:
            self.logger.warning(f"Signal direction not supported: {signal.direction}")
            return False
    
    def _execute_long_entry(self, signal: Signal) -> bool:
        """Execute long entry."""
        # Get account info
        account = self.broker.get_account_info()
        buying_power = account["buying_power"]
        
        # Determine quantity (use confidence as position sizing)
        base_qty = Decimal("10")  # Base quantity
        quantity = base_qty * signal.confidence
        quantity = quantity.quantize(Decimal("1"))  # Round to whole shares
        
        if quantity < 1:
            self.logger.info("Quantity too small after position sizing")
            return False
        
        entry_price = signal.entry_price or Decimal("0")
        
        # Risk check
        risk_result = self.risk.check_order(
            symbol=signal.symbol,
            quantity=quantity,
            side="BUY",
            entry_price=entry_price,
            buying_power=buying_power
        )
        
        if not risk_result.approved:
            self.logger.warning(f"Risk check failed: {risk_result.reason}")
            return False
        
        # Generate order ID
        order_id = self._generate_order_id()
        
        # Submit order
        try:
            broker_order_id = self.execution.submit_market_order(
                internal_order_id=order_id,
                symbol=signal.symbol,
                quantity=quantity,
                side=BrokerOrderSide.BUY,
                strategy=signal.strategy_name,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit
            )
            
            self.logger.info(f"Long entry executed: {signal.symbol} x{quantity}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to execute long entry: {e}", exc_info=True)
            return False
    
    def _execute_long_exit(self, signal: Signal, position: Position) -> bool:
        """Execute long exit."""
        order_id = self._generate_order_id()
        
        try:
            broker_order_id = self.execution.submit_market_order(
                internal_order_id=order_id,
                symbol=signal.symbol,
                quantity=position.quantity,
                side=BrokerOrderSide.SELL,
                strategy=signal.strategy_name
            )
            
            self.logger.info(f"Long exit executed: {signal.symbol} x{position.quantity}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to execute long exit: {e}", exc_info=True)
            return False
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID using injected clock."""
        self._order_counter += 1
        timestamp = self.clock.now().strftime("%Y%m%d_%H%M%S")
        return f"ORD_{timestamp}_{self._order_counter:04d}"
