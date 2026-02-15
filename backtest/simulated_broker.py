"""
Simulated broker for backtesting.

LEAN COMPATIBILITY:
Based on QuantConnect's BrokerageModel + TransactionHandler.

ARCHITECTURE:
- Simulates broker behavior
- Order management (pending orders)
- Fill simulation with slippage
- Commission calculation
- Portfolio tracking
- Buying power enforcement

Matches live broker interface.
"""

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass, field
import uuid

from core.brokers import BrokerOrderSide
from core.state import Position
from backtest.fill_models import (
    FillModel,
    ImmediateFillModel,
    OrderType,
    AssetClass
)
from backtest.fee_models import FeeModel, AlpacaFeeModel
from core.logging import get_logger, LogStream


# ============================================================================
# SIMULATED ORDER
# ============================================================================

@dataclass
class SimulatedOrder:
    """Simulated order in backtest."""
    order_id: str
    symbol: str
    side: BrokerOrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    fill_price: Optional[Decimal] = None
    status: str = "PENDING"  # PENDING, FILLED, CANCELLED


# ============================================================================
# SIMULATED BROKER
# ============================================================================

class SimulatedBroker:
    """
    Simulated broker for backtesting.
    
    FEATURES:
    - Realistic fill simulation
    - Commission calculation
    - Portfolio tracking
    - Buying power management
    - Order book management
    
    USAGE:
        broker = SimulatedBroker(
            starting_cash=100000,
            fill_model=ImmediateFillModel(),
            fee_model=AlpacaFeeModel()
        )
        
        order_id = broker.submit_order("SPY", BUY, 100)
        broker.process_bar("SPY", bar, timestamp)
    """
    
    def __init__(
        self,
        starting_cash: Decimal,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
        asset_class: AssetClass = AssetClass.EQUITY
    ):
        """
        Initialize simulated broker.
        
        Args:
            starting_cash: Starting capital
            fill_model: Fill simulation model
            fee_model: Commission model
            asset_class: Asset class
        """
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.asset_class = asset_class
        
        # Models
        self.fill_model = fill_model or ImmediateFillModel()
        self.fee_model = fee_model or AlpacaFeeModel()
        
        # Order management
        self.pending_orders: Dict[str, SimulatedOrder] = {}
        self.filled_orders: List[SimulatedOrder] = []
        self.cancelled_orders: List[SimulatedOrder] = []
        
        # Positions: {symbol: Position}
        self.positions: Dict[str, Position] = {}
        
        # Performance tracking
        self.total_commission = Decimal("0")
        self.trade_count = 0
        
        self.logger = get_logger(LogStream.SYSTEM)
        
        self.logger.info("SimulatedBroker initialized", extra={
            "starting_cash": float(starting_cash),
            "asset_class": asset_class.value
        })
    
    def submit_order(
        self,
        symbol: str,
        side: BrokerOrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None
    ) -> str:
        """
        Submit order.
        
        Args:
            symbol: Symbol
            side: BUY or SELL
            quantity: Quantity
            order_type: Order type
            limit_price: Limit price (if applicable)
            stop_price: Stop price (if applicable)
            
        Returns:
            Order ID
        """
        order_id = str(uuid.uuid4())
        
        order = SimulatedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            created_at=datetime.now(timezone.utc)
        )
        
        self.pending_orders[order_id] = order
        
        self.logger.debug(f"Order submitted: {order_id}", extra={
            "symbol": symbol,
            "side": side.value,
            "quantity": float(quantity)
        })
        
        return order_id
    
    def process_bar(
        self,
        symbol: str,
        bar: dict,
        timestamp: datetime
    ) -> List[SimulatedOrder]:
        """
        Process bar and attempt to fill pending orders.
        
        Args:
            symbol: Symbol
            bar: OHLCV bar
            timestamp: Bar timestamp
            
        Returns:
            List of filled orders
        """
        filled_this_bar = []
        
        # Check each pending order for this symbol
        for order_id, order in list(self.pending_orders.items()):
            if order.symbol != symbol:
                continue
            
            # Attempt fill
            fill_result = self.fill_model.fill(
                order_type=order.order_type,
                side=order.side,
                quantity=order.quantity,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                current_bar=bar,
                timestamp=timestamp
            )
            
            if fill_result:
                fill_price, fill_quantity, fill_time = fill_result
                
                # Calculate commission
                commission = self.fee_model.get_fee(
                    asset_class=self.asset_class,
                    side=order.side,
                    quantity=fill_quantity,
                    price=fill_price
                )
                
                # Update order
                order.status = "FILLED"
                order.filled_at = fill_time
                order.fill_price = fill_price
                
                # Update positions
                self._update_position(
                    symbol=symbol,
                    side=order.side,
                    quantity=fill_quantity,
                    price=fill_price
                )
                
                # Update cash
                trade_value = fill_quantity * fill_price
                
                if order.side == BrokerOrderSide.BUY:
                    self.cash -= (trade_value + commission)
                else:
                    self.cash += (trade_value - commission)
                
                # Track commission
                self.total_commission += commission
                self.trade_count += 1
                
                # Move to filled
                del self.pending_orders[order_id]
                self.filled_orders.append(order)
                filled_this_bar.append(order)
                
                self.logger.info(f"Order filled: {order_id}", extra={
                    "symbol": symbol,
                    "side": order.side.value,
                    "quantity": float(fill_quantity),
                    "price": float(fill_price),
                    "commission": float(commission)
                })
        
        return filled_this_bar
    
    def _update_position(
        self,
        symbol: str,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ):
        """Update position after fill."""
        if symbol not in self.positions:
            # New position
            if side == BrokerOrderSide.BUY:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=price
                )
            else:
                # Short position
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=-quantity,
                    average_cost=price
                )
        else:
            # Existing position
            pos = self.positions[symbol]
            
            if side == BrokerOrderSide.BUY:
                # Adding to long or covering short
                new_quantity = pos.quantity + quantity
                
                if pos.quantity >= 0:
                    # Adding to long
                    new_cost = (pos.average_cost * pos.quantity + price * quantity) / new_quantity
                    pos.quantity = new_quantity
                    pos.average_cost = new_cost
                else:
                    # Covering short
                    if new_quantity == 0:
                        # Flat - remove position
                        del self.positions[symbol]
                    elif new_quantity > 0:
                        # Flipped to long
                        pos.quantity = new_quantity
                        pos.average_cost = price
                    else:
                        # Still short
                        pos.quantity = new_quantity
            
            else:  # SELL
                # Adding to short or closing long
                new_quantity = pos.quantity - quantity
                
                if pos.quantity <= 0:
                    # Adding to short
                    new_cost = (pos.average_cost * abs(pos.quantity) + price * quantity) / abs(new_quantity)
                    pos.quantity = new_quantity
                    pos.average_cost = new_cost
                else:
                    # Closing long
                    if new_quantity == 0:
                        # Flat - remove position
                        del self.positions[symbol]
                    elif new_quantity < 0:
                        # Flipped to short
                        pos.quantity = new_quantity
                        pos.average_cost = price
                    else:
                        # Still long
                        pos.quantity = new_quantity
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for symbol."""
        return self.positions.get(symbol)
    
    def get_portfolio_value(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """
        Calculate total portfolio value.
        
        Args:
            current_prices: {symbol: current_price}
            
        Returns:
            Total portfolio value
        """
        equity = self.cash
        
        for symbol, position in self.positions.items():
            if symbol in current_prices:
                market_value = position.quantity * current_prices[symbol]
                equity += market_value
        
        return equity
    
    def get_buying_power(self) -> Decimal:
        """Get available buying power."""
        # Simplified: just return cash
        # In production, would account for margin
        return self.cash
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        if order_id in self.pending_orders:
            order = self.pending_orders[order_id]
            order.status = "CANCELLED"
            del self.pending_orders[order_id]
            self.cancelled_orders.append(order)
            return True
        return False
