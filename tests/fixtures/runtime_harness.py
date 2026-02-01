"""
Acceptance test runtime harness - PRODUCTION-GRADE

This harness initializes the REAL system with all production components
except the external broker I/O, which is stubbed at the boundary.

DESIGN PRINCIPLES:
- Use real Container initialization
- Use real configuration files
- Stub ONLY external broker API (Alpaca)
- No mocks of internal helpers
- No monkeypatching

USAGE:
    from tests.fixtures.runtime_harness import AcceptanceHarness
    
    harness = AcceptanceHarness(config_path="config/config_micro.yaml")
    harness.initialize()
    
    # Inject signal
    harness.inject_signal({
        "symbol": "SPY",
        "side": "BUY",
        "quantity": 10,
        "action": "ENTRY",
        "strategy": "TestStrategy"
    })
    
    # Run one cycle
    harness.run_one_cycle()
    
    # Verify outcomes
    assert harness.get_position("SPY") is not None
    assert harness.get_open_orders("SPY") == []
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd

# Real system imports
from core.di.container import Container
from core.brokers.alpaca_connector import BrokerOrderSide
from core.state import OrderStatus, Position
from core.data.contract import MarketDataContract


# ============================================================================
# BROKER STUB (Boundary Stub - Minimal External I/O Mock)
# ============================================================================

@dataclass
class StubOrder:
    """Order representation for stub broker."""
    id: str
    client_order_id: str
    symbol: str
    qty: Decimal
    side: str  # "BUY" or "SELL"
    status: str  # Alpaca status string
    filled_qty: Decimal = Decimal("0")
    filled_avg_price: Optional[Decimal] = None
    filled_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class StubPosition:
    """Position representation for stub broker."""
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    current_price: Decimal = Decimal("100.0")
    
    @property
    def market_value(self) -> Decimal:
        return self.qty * self.current_price
    
    @property
    def unrealized_pl(self) -> Decimal:
        return (self.current_price - self.avg_entry_price) * self.qty


class StubBrokerConnector:
    """
    Stub broker connector that simulates Alpaca API at the boundary.
    
    CRITICAL: This is the ONLY component we mock.
    All internal system components use real implementations.
    
    BEHAVIOR:
    - submit_market_order() → immediately fills at current price
    - submit_limit_order() → creates pending order
    - get_order_status() → returns order state
    - get_position() → returns position
    - list_positions() → returns all positions
    - get_account_info() → returns simulated account
    """
    
    def __init__(self, initial_equity: Decimal = Decimal("10000.0")):
        self.equity = initial_equity
        self.buying_power = initial_equity
        self.orders: Dict[str, StubOrder] = {}
        self.positions: Dict[str, StubPosition] = {}
        self._order_counter = 0
        self._current_prices: Dict[str, Decimal] = {}  # Symbol -> price
        
    def set_market_price(self, symbol: str, price: Decimal):
        """Set current market price for a symbol."""
        self._current_prices[symbol] = price
    
    def get_market_price(self, symbol: str) -> Decimal:
        """Get current market price (defaults to $100)."""
        return self._current_prices.get(symbol, Decimal("100.0"))
    
    def submit_market_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        internal_order_id: str
    ) -> str:
        """Submit market order - IMMEDIATELY FILLS."""
        self._order_counter += 1
        broker_id = f"STUB_{self._order_counter:06d}"
        
        fill_price = self.get_market_price(symbol)
        
        order = StubOrder(
            id=broker_id,
            client_order_id=internal_order_id,
            symbol=symbol,
            qty=quantity,
            side="buy" if side == BrokerOrderSide.BUY else "sell",
            status="filled",
            filled_qty=quantity,
            filled_avg_price=fill_price,
            filled_at=datetime.now(timezone.utc)
        )
        
        self.orders[broker_id] = order
        
        # Update positions
        if side == BrokerOrderSide.BUY:
            if symbol in self.positions:
                pos = self.positions[symbol]
                total_qty = pos.qty + quantity
                total_cost = (pos.avg_entry_price * pos.qty) + (fill_price * quantity)
                pos.qty = total_qty
                pos.avg_entry_price = total_cost / total_qty
            else:
                self.positions[symbol] = StubPosition(
                    symbol=symbol,
                    qty=quantity,
                    avg_entry_price=fill_price,
                    current_price=fill_price
                )
        else:  # SELL
            if symbol in self.positions:
                self.positions[symbol].qty -= quantity
                if self.positions[symbol].qty <= 0:
                    del self.positions[symbol]
        
        return broker_id
    
    def submit_limit_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        limit_price: Decimal,
        internal_order_id: str
    ) -> str:
        """Submit limit order - stays PENDING."""
        self._order_counter += 1
        broker_id = f"STUB_{self._order_counter:06d}"
        
        order = StubOrder(
            id=broker_id,
            client_order_id=internal_order_id,
            symbol=symbol,
            qty=quantity,
            side="buy" if side == BrokerOrderSide.BUY else "sell",
            status="accepted"  # Alpaca status for pending
        )
        
        self.orders[broker_id] = order
        return broker_id
    
    def submit_stop_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        stop_price: Decimal,
        internal_order_id: str
    ) -> str:
        """Submit stop order - stays PENDING."""
        self._order_counter += 1
        broker_id = f"STUB_{self._order_counter:06d}"
        
        order = StubOrder(
            id=broker_id,
            client_order_id=internal_order_id,
            symbol=symbol,
            qty=quantity,
            side="buy" if side == BrokerOrderSide.BUY else "sell",
            status="accepted"
        )
        
        self.orders[broker_id] = order
        return broker_id
    
    def get_order_status(self, broker_order_id: str) -> Tuple[OrderStatus, Optional[Dict]]:
        """Get order status."""
        order = self.orders.get(broker_order_id)
        if not order:
            return OrderStatus.REJECTED, None
        
        # Map Alpaca status to OrderStatus
        status_map = {
            "new": OrderStatus.SUBMITTED,
            "accepted": OrderStatus.SUBMITTED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.EXPIRED
        }
        
        status = status_map.get(order.status, OrderStatus.PENDING)
        
        fill_info = None
        if order.filled_qty and order.filled_qty > 0:
            fill_info = {
                "filled_qty": order.filled_qty,
                "filled_avg_price": order.filled_avg_price,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None
            }
        
        return status, fill_info
    
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order."""
        order = self.orders.get(broker_order_id)
        if order and order.status not in ("filled", "canceled", "rejected"):
            order.status = "canceled"
            return True
        return False
    
    def get_position(self, symbol: str):
        """Get position for symbol."""
        pos = self.positions.get(symbol)
        if not pos:
            return None
        
        # Return object with attributes expected by system
        class PosObj:
            def __init__(self, p: StubPosition):
                self.symbol = p.symbol
                self.qty = p.qty
                self.quantity = p.qty
                self.position_qty = p.qty
                self.avg_entry_price = p.avg_entry_price
                self.market_value = p.market_value
                self.unrealized_pl = p.unrealized_pl
        
        return PosObj(pos)
    
    def list_positions(self) -> List:
        """List all positions."""
        return [self.get_position(sym) for sym in self.positions.keys()]
    
    def get_positions(self) -> List:
        """Alias for list_positions."""
        return self.list_positions()
    
    def list_orders(self, status: Optional[str] = None) -> List:
        """List orders."""
        orders = list(self.orders.values())
        if status == "open":
            orders = [o for o in orders if o.status not in ("filled", "canceled", "rejected", "expired")]
        return orders
    
    def list_open_orders(self) -> List:
        """List open orders."""
        return self.list_orders(status="open")
    
    def get_orders(self) -> List:
        """Get all orders (for reconciliation)."""
        # Return objects with expected attributes
        class OrderObj:
            def __init__(self, o: StubOrder):
                self.id = o.id
                self.client_order_id = o.client_order_id
                self.symbol = o.symbol
                self.qty = o.qty
                self.side = o.side
                self.status = o.status
        
        return [OrderObj(o) for o in self.orders.values()]
    
    def get_account_info(self) -> Dict:
        """Get account information."""
        # Calculate portfolio value
        portfolio_value = self.equity
        for pos in self.positions.values():
            portfolio_value += pos.unrealized_pl
        
        return {
            "portfolio_value": str(portfolio_value),
            "buying_power": str(self.buying_power),
            "cash": str(self.equity),
            "equity": str(portfolio_value)
        }
    
    def get_bars(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Get bars - returns synthetic data."""
        # Return simple OHLCV bars at current price
        price = self.get_market_price(symbol)
        
        # Create timestamps (last N minutes)
        end = datetime.now(timezone.utc)
        timestamps = pd.date_range(end=end, periods=limit, freq='1min')
        
        # Create bars with slight variation
        df = pd.DataFrame({
            'open': [price] * limit,
            'high': [price * Decimal("1.001")] * limit,
            'low': [price * Decimal("0.999")] * limit,
            'close': [price] * limit,
            'volume': [1000] * limit
        }, index=timestamps)
        
        return df


# ============================================================================
# ACCEPTANCE HARNESS
# ============================================================================

class AcceptanceHarness:
    """
    Production-grade acceptance test harness.
    
    Initializes the REAL system with all components except broker I/O.
    """
    
    def __init__(self, config_path: str = "config/config_micro.yaml"):
        self.config_path = Path(config_path)
        self.container: Optional[Container] = None
        self.broker: Optional[StubBrokerConnector] = None
        self._signal_queue: List[Dict] = []
        
    def initialize(self):
        """Initialize the real system with stub broker."""
        # Create stub broker with sufficient capital for testing
        self.broker = StubBrokerConnector(initial_equity=Decimal("100000.0"))
        
        # Initialize real container
        self.container = Container()
        self.container.initialize(str(self.config_path))
        
        # CRITICAL: Clear any existing positions/orders from previous tests
        position_store = self.container.get_position_store()
        position_store.clear()
        
        # Inject stub broker
        self.container.set_broker_connector(self.broker)
        
        # Start services
        self.container.start()
        
        # ACCEPTANCE TEST CONFIGURATION:
        # Disable time-based protections since tests run outside market hours
        protections = self.container.get_protections()
        protections.disable_protection("TimeWindow")
        protections.disable_protection("VolatilityHalt")  # Disable volatility (no real market data)
        
        # Override risk limits for testing (remove restrictive micro-account limits)
        risk_manager = self.container.get_risk_manager()
        risk_manager.limits.min_buying_power_reserve = Decimal("0")
        risk_manager.limits.max_position_size_usd = Decimal("50000")  # Allow up to $50k positions
        risk_manager.limits.max_position_pct_portfolio = Decimal("1.0")  # Allow 100% of portfolio
        risk_manager.limits.max_positions = 10  # Allow multiple positions for testing
    
    def inject_signal(self, signal: Dict[str, Any]):
        """
        Inject a signal to be processed on next cycle.
        
        Signal format:
        {
            "symbol": "SPY",
            "side": "BUY" | "SELL",
            "quantity": 10,
            "action": "ENTRY" | "EXIT",
            "strategy": "TestStrategy",
            "stop_loss": 99.5,  # Optional
            "take_profit": 101.0,  # Optional
        }
        """
        self._signal_queue.append(signal)
    
    def set_market_price(self, symbol: str, price: Decimal):
        """Set market price for symbol."""
        if self.broker:
            self.broker.set_market_price(symbol, price)
    
    def run_one_cycle(self):
        """
        Run one trading cycle.
        
        This processes queued signals through the real execution pipeline:
        1. Protection checks
        2. Risk validation
        3. Order execution
        4. Position updates
        """
        if not self.container or not self.broker:
            raise RuntimeError("Harness not initialized")
        
        # Get components
        exec_engine = self.container.get_order_execution_engine()
        risk_manager = self.container.get_risk_manager()
        protections = self.container.get_protections()
        position_store = self.container.get_position_store()
        
        # Get account info
        acct = self.broker.get_account_info()
        account_value = Decimal(acct["portfolio_value"])
        buying_power = Decimal(acct["buying_power"])
        
        # Process each signal
        for signal in self._signal_queue:
            symbol = signal["symbol"]
            side_str = signal["side"]
            qty = Decimal(str(signal["quantity"]))
            strategy = signal.get("strategy", "TestStrategy")
            
            # Convert side
            broker_side = BrokerOrderSide.BUY if side_str == "BUY" else BrokerOrderSide.SELL
            
            # Get current price
            price = self.broker.get_market_price(symbol)
            
            # Protection check
            prot_result = protections.check(symbol=symbol, current_trades=None, completed_trades=None)
            if prot_result.is_protected:
                continue
            
            # Risk validation
            risk = risk_manager.validate_trade(
                symbol=symbol,
                quantity=qty,
                side=broker_side,
                price=price,
                account_value=account_value,
                buying_power=buying_power,
                strategy=strategy
            )
            
            if not risk.approved:
                continue
            
            # Submit order
            internal_id = f"TEST_{symbol}_{datetime.now(timezone.utc).timestamp()}"
            
            stop_loss = signal.get("stop_loss")
            take_profit = signal.get("take_profit")
            if stop_loss:
                stop_loss = Decimal(str(stop_loss))
            if take_profit:
                take_profit = Decimal(str(take_profit))
            
            # Create order in state machine FIRST
            order_machine = self.container.get_order_machine()
            order_machine.create_order(
                order_id=internal_id,
                symbol=symbol,
                quantity=qty,
                side="LONG" if broker_side == BrokerOrderSide.BUY else "SHORT",
                order_type="MARKET",
                strategy=strategy,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            # Then submit to broker
            broker_order_id = exec_engine.submit_market_order(
                internal_order_id=internal_id,
                symbol=symbol,
                quantity=qty,
                side=broker_side,
                strategy=strategy,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            # Wait for fill
            final_status = exec_engine.wait_for_order(
                internal_order_id=internal_id,
                broker_order_id=broker_order_id,
                timeout_seconds=5,
                poll_interval=0.1
            )
        
        # Clear queue
        self._signal_queue.clear()
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position from position store."""
        if not self.container:
            return None
        position_store = self.container.get_position_store()
        return position_store.get(symbol)
    
    def get_all_positions(self) -> List[Position]:
        """Get all positions."""
        if not self.container:
            return []
        position_store = self.container.get_position_store()
        return position_store.get_all()
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List:
        """Get open orders from broker."""
        if not self.broker:
            return []
        orders = self.broker.list_open_orders()
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders
    
    def get_order_machine(self):
        """Get order state machine."""
        return self.container.get_order_machine() if self.container else None
    
    def shutdown(self):
        """Shutdown the harness."""
        if self.container:
            self.container.stop()
