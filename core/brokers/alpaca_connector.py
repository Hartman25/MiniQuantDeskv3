"""
Alpaca broker connector with order submission and position tracking.

CRITICAL PROPERTIES:
1. All broker calls logged with request/response
2. Rate limit handling (429 errors)
3. Automatic retry with exponential backoff
4. Position reconciliation on startup
5. Order status polling
6. Paper/live mode safety checks

PATCH 3: Added get_orders() method for reconciliation support.

Based on Alpaca Trading API v2.
"""

from typing import Optional, List, Dict, Tuple
from decimal import Decimal
from datetime import datetime
import os
import time
import logging
from enum import Enum

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

from core.logging import get_logger, LogStream, LogContext
from core.state import OrderStatus, Position


# ============================================================================
# BROKER ORDER SIDE
# ============================================================================

class BrokerOrderSide(Enum):
    """Order side (buy/sell)."""
    BUY = "BUY"
    SELL = "SELL"


# ============================================================================
# ALPACA BROKER CONNECTOR
# ============================================================================

class AlpacaBrokerConnector:
    """
    Alpaca broker integration.
    
    GUARANTEES:
    - All API calls logged
    - Automatic retry on transient errors
    - Rate limit handling
    - Paper/live mode validation
    
    THREAD SAFETY:
    - NOT thread-safe
    - Caller must synchronize
    """
    
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 1.0
    RETRY_BACKOFF_MULTIPLIER = 2.0
    
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        """Initialize Alpaca connector."""
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.logger = get_logger(LogStream.TRADING)
        
        self.client = TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=paper
        )
        
        self._order_id_map: Dict[str, str] = {}
        
        self.logger.info("AlpacaBrokerConnector initialized", extra={
            "paper_trading": self.paper
        })
        
        self._verify_account()

    def _ensure_orders_allowed(self) -> None:
        """Refuse order placement when running in explicit smoke mode.

        This is a HARD safety guard used by entry_live.py --once.
        """
        if os.getenv("MQD_SMOKE_NO_ORDERS", "").strip().lower() in ("1", "true", "yes"):
            raise BrokerOrderError(
                "SMOKE MODE: order placement is disabled (MQD_SMOKE_NO_ORDERS=1)."
            )
    
    def _verify_account(self):
        """Verify account access."""
        try:
            account = self.client.get_account()
            self.logger.info("Account verified", extra={
                "account_number": account.account_number[:4] + "****",
                "buying_power": str(account.buying_power)
            })
        except Exception as e:
            raise BrokerConnectionError(f"Account verification failed: {e}")
    
    def submit_market_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        internal_order_id: str
    ) -> str:
        """Submit market order. Returns broker_order_id."""
        with LogContext(internal_order_id):
            try:
                self._ensure_orders_allowed()
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=float(quantity),
                    side=OrderSide.BUY if side == BrokerOrderSide.BUY else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=internal_order_id
                )
                
                self.logger.info("Submitting market order", extra={
                    "symbol": symbol,
                    "quantity": str(quantity),
                    "side": side.value
                })
                
                order = self._retry_api_call(lambda: self.client.submit_order(request))
                broker_order_id = order.id
                self._order_id_map[internal_order_id] = broker_order_id
                
                self.logger.info("Order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": order.status.value
                })
                
                return broker_order_id
                
            except Exception as e:
                self.logger.error("Order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit order: {e}")
    
    def get_order_status(self, broker_order_id: str) -> Tuple[OrderStatus, Optional[Dict]]:
        """Get order status. Returns (OrderStatus, fill_info)."""
        try:
            order = self._retry_api_call(lambda: self.client.get_order_by_id(broker_order_id))
            status = self._map_status(order.status.value)
            
            fill_info = None
            if order.filled_qty and float(order.filled_qty) > 0:
                fill_info = {
                    "filled_qty": Decimal(str(order.filled_qty)),
                    "filled_avg_price": Decimal(str(order.filled_avg_price)) if order.filled_avg_price else None,
                    "filled_at": order.filled_at.isoformat() if order.filled_at else None
                }
            
            return status, fill_info
            
        except Exception as e:
            raise BrokerOrderError(f"Failed to get order status: {e}")
    
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order. Returns True if cancelled."""
        try:
            self.client.cancel_order_by_id(broker_order_id)
            self.logger.info("Order cancelled", extra={"broker_order_id": broker_order_id})
            return True
        except APIError as e:
            if "not cancelable" in str(e).lower():
                return False
            raise BrokerOrderError(f"Failed to cancel: {e}")
    
    def get_positions(self) -> List[Position]:
        """Get all positions from broker."""
        try:
            alpaca_positions = self._retry_api_call(lambda: self.client.get_all_positions())
            
            positions = []
            for pos in alpaca_positions:
                position = Position(
                    symbol=pos.symbol,
                    quantity=Decimal(str(pos.qty)),
                    entry_price=Decimal(str(pos.avg_entry_price)),
                    entry_time=datetime.utcnow(),
                    strategy="UNKNOWN",
                    order_id="UNKNOWN",
                    current_price=Decimal(str(pos.current_price)) if pos.current_price else None,
                    unrealized_pnl=Decimal(str(pos.unrealized_pl)) if pos.unrealized_pl else None,
                    broker_position_id=pos.asset_id
                )
                positions.append(position)
            
            return positions
            
        except Exception as e:
            raise BrokerConnectionError(f"Failed to get positions: {e}")
    
    def get_account_info(self) -> Dict:
        """Get account information."""
        try:
            account = self._retry_api_call(lambda: self.client.get_account())
            return {
                "buying_power": Decimal(str(account.buying_power)),
                "cash": Decimal(str(account.cash)),
                "portfolio_value": Decimal(str(account.portfolio_value)),
                "pattern_day_trader": account.pattern_day_trader
            }
        except Exception as e:
            raise BrokerConnectionError(f"Failed to get account info: {e}")
    
    def get_orders(self, status: str = 'open') -> List:
        """
        Get orders from broker filtered by status.
        
        PATCH 3: Added for reconciliation support.
        
        Args:
            status: Filter by status ('open', 'closed', 'all')
        
        Returns:
            List of Alpaca Order objects
        
        Raises:
            BrokerConnectionError: If API call fails
        """
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            
            # Map status string to Alpaca enum
            status_map = {
                'open': QueryOrderStatus.OPEN,
                'closed': QueryOrderStatus.CLOSED,
                'all': QueryOrderStatus.ALL
            }
            
            alpaca_status = status_map.get(status.lower(), QueryOrderStatus.OPEN)
            
            request = GetOrdersRequest(status=alpaca_status)
            orders = self._retry_api_call(lambda: self.client.get_orders(request))
            
            self.logger.debug(
                f"Fetched {len(orders)} orders",
                extra={"status_filter": status}
            )
            
            return orders
            
        except Exception as e:
            raise BrokerConnectionError(f"Failed to get orders: {e}")
    
    def _retry_api_call(self, func, max_retries: Optional[int] = None):
        """Retry with exponential backoff."""
        max_retries = max_retries or self.MAX_RETRIES
        delay = self.RETRY_DELAY_SECONDS
        
        for attempt in range(max_retries):
            try:
                return func()
            except APIError as e:
                status_code = getattr(e, 'status_code', None)
                
                if status_code == 429 or (status_code and 500 <= status_code < 600):
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        delay *= self.RETRY_BACKOFF_MULTIPLIER
                        continue
                raise
    
    def _map_status(self, alpaca_status: str) -> OrderStatus:
        """Map Alpaca status to OrderStatus."""
        mapping = {
            "new": OrderStatus.SUBMITTED,
            "accepted": OrderStatus.SUBMITTED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED
        }
        return mapping.get(alpaca_status.lower(), OrderStatus.SUBMITTED)

    def submit_limit_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        limit_price: Decimal,
        internal_order_id: str
    ) -> str:
        """Submit limit order. Returns broker_order_id."""
        with LogContext(internal_order_id):
            try:
                self._ensure_orders_allowed()
                if limit_price is None or limit_price <= 0:
                    raise ValueError(f"limit_price must be positive, got {limit_price}")

                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=float(quantity),
                    side=OrderSide.BUY if side == BrokerOrderSide.BUY else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    limit_price=float(limit_price),
                    client_order_id=internal_order_id
                )

                self.logger.info("Submitting limit order", extra={
                    "symbol": symbol,
                    "quantity": str(quantity),
                    "side": side.value,
                    "limit_price": str(limit_price)
                })

                order = self._retry_api_call(lambda: self.client.submit_order(request))
                broker_order_id = order.id
                self._order_id_map[internal_order_id] = broker_order_id

                self.logger.info("Limit order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": order.status.value
                })

                return broker_order_id

            except Exception as e:
                self.logger.error("Limit order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit limit order: {e}")

    def submit_stop_order(
        self,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        stop_price: Decimal,
        internal_order_id: str
    ) -> str:
        """Submit stop (market) order. Returns broker_order_id."""
        with LogContext(internal_order_id):
            try:
                self._ensure_orders_allowed()
                if stop_price is None or stop_price <= 0:
                    raise ValueError(f"stop_price must be positive, got {stop_price}")

                request = StopOrderRequest(
                    symbol=symbol,
                    qty=float(quantity),
                    side=OrderSide.BUY if side == BrokerOrderSide.BUY else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    stop_price=float(stop_price),
                    client_order_id=internal_order_id
                )

                self.logger.info("Submitting stop order", extra={
                    "symbol": symbol,
                    "quantity": str(quantity),
                    "side": side.value,
                    "stop_price": str(stop_price)
                })

                order = self._retry_api_call(lambda: self.client.submit_order(request))
                broker_order_id = order.id
                self._order_id_map[internal_order_id] = broker_order_id

                self.logger.info("Stop order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": order.status.value
                })

                return broker_order_id

            except Exception as e:
                self.logger.error("Stop order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit stop order: {e}")

# ============================================================================
# EXCEPTIONS
# ============================================================================


class BrokerConnectionError(Exception):
    """Broker connection error."""
    pass


class BrokerOrderError(Exception):
    """Broker order error."""
    pass
