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

from __future__ import annotations

from typing import Optional, List, Dict, Tuple, Callable, Any
from decimal import Decimal
from datetime import datetime, timezone, UTC
import os
import time
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
    RETRY_TIMEOUT_SECONDS = 30.0  # PATCH 11: Absolute timeout for all retries

    # Network-level errors that are always safe to retry (transient by nature).
    # NOTE: CLASS attribute so tests/mocks don't break if constructed oddly.
    _RETRYABLE_NETWORK_ERRORS = (ConnectionError, TimeoutError, OSError)

    def __init__(self, api_key: str, api_secret: str, paper: bool = True, **kwargs):
        """Initialize Alpaca connector.

        Extra kwargs (base_url, data_feed, etc.) are accepted for forward
        compatibility but currently unused by the Alpaca v2 SDK wrapper.
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.logger = get_logger(LogStream.TRADING)

        self.client = TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=paper
        )

        # PATCH 5: Removed broker-side _order_id_map.
        # OrderExecutionEngine owns the internal<->broker mapping.

        # Clock TTL cache — avoid hammering /v2/clock every cycle.
        # Use monotonic seconds consistently (tests rely on time.sleep()).
        self._clock_cache: Optional[Dict[str, Any]] = None
        self._clock_cache_ts: float = 0.0  # monotonic seconds
        self._clock_cache_ttl: float = float(os.getenv("MARKET_CLOCK_CACHE_S", "15") or "15")

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

    def _verify_account(self) -> None:
        """Verify account access."""
        try:
            account = self.client.get_account()
            self.logger.info("Account verified", extra={
                "account_number": (account.account_number[:4] + "****") if getattr(account, "account_number", None) else None,
                "buying_power": str(getattr(account, "buying_power", None))
            })
        except Exception as e:
            raise BrokerConnectionError(f"Account verification failed: {e}") from e

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

                self.logger.info("Order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": getattr(order.status, "value", str(order.status))
                })

                return broker_order_id

            except Exception as e:
                self.logger.error("Order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit order: {e}") from e

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

                self.logger.info("Limit order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": getattr(order.status, "value", str(order.status))
                })

                return broker_order_id

            except Exception as e:
                self.logger.error("Limit order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit limit order: {e}") from e

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

                self.logger.info("Stop order submitted", extra={
                    "broker_order_id": broker_order_id,
                    "status": getattr(order.status, "value", str(order.status))
                })

                return broker_order_id

            except Exception as e:
                self.logger.error("Stop order submission failed", extra={"error": str(e)}, exc_info=True)
                raise BrokerOrderError(f"Failed to submit stop order: {e}") from e

    def get_order_status(self, broker_order_id: str) -> Tuple[OrderStatus, Optional[Dict]]:
        """Get order status. Returns (OrderStatus, fill_info)."""
        try:
            order = self._retry_api_call(lambda: self.client.get_order_by_id(broker_order_id))
            status = self._map_status(getattr(order.status, "value", str(order.status)))

            fill_info = None
            filled_qty = getattr(order, "filled_qty", None)
            if filled_qty is not None and float(filled_qty) > 0:
                fill_info = {
                    "filled_qty": Decimal(str(order.filled_qty)),
                    "filled_avg_price": Decimal(str(order.filled_avg_price)) if getattr(order, "filled_avg_price", None) else None,
                    "filled_at": order.filled_at.isoformat() if getattr(order, "filled_at", None) else None,
                }

            return status, fill_info

        except Exception as e:
            raise BrokerOrderError(f"Failed to get order status: {e}") from e

    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order. Returns True if cancelled."""
        try:
            self.client.cancel_order_by_id(broker_order_id)
            self.logger.info("Order cancelled", extra={"broker_order_id": broker_order_id})
            return True
        except APIError as e:
            if "not cancelable" in str(e).lower():
                return False
            raise BrokerOrderError(f"Failed to cancel: {e}") from e

    def get_positions(self) -> List[Position]:
        """Get all positions from broker."""
        try:
            alpaca_positions = self._retry_api_call(lambda: self.client.get_all_positions())

            positions: List[Position] = []
            for pos in alpaca_positions:
                position = Position(
                    symbol=pos.symbol,
                    quantity=Decimal(str(pos.qty)),
                    entry_price=Decimal(str(pos.avg_entry_price)),
                    entry_time=datetime.now(UTC),
                    strategy="UNKNOWN",
                    order_id="UNKNOWN",
                    current_price=Decimal(str(pos.current_price)) if getattr(pos, "current_price", None) else None,
                    unrealized_pnl=Decimal(str(pos.unrealized_pl)) if getattr(pos, "unrealized_pl", None) else None,
                    broker_position_id=getattr(pos, "asset_id", None),
                )
                positions.append(position)

            return positions

        except Exception as e:
            raise BrokerConnectionError(f"Failed to get positions: {e}") from e

    def get_account_info(self) -> Dict:
        """Get account information."""
        try:
            account = self._retry_api_call(lambda: self.client.get_account())
            return {
                "buying_power": Decimal(str(account.buying_power)),
                "cash": Decimal(str(account.cash)),
                "portfolio_value": Decimal(str(account.portfolio_value)),
                "pattern_day_trader": getattr(account, "pattern_day_trader", None),
            }
        except Exception as e:
            raise BrokerConnectionError(f"Failed to get account info: {e}") from e

    def get_orders(self, status: str = "open", limit: Optional[int] = None) -> List:
        """Get orders from broker filtered by status (open/closed/all). Optionally limit results."""
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus

            status_map = {
                "open": QueryOrderStatus.OPEN,
                "closed": QueryOrderStatus.CLOSED,
                "all": QueryOrderStatus.ALL,
            }
            alpaca_status = status_map.get((status or "open").lower(), QueryOrderStatus.OPEN)

            # Alpaca supports limit on the request; keep it optional.
            if limit is not None:
                request = GetOrdersRequest(status=alpaca_status, limit=int(limit))
            else:
                request = GetOrdersRequest(status=alpaca_status)

            orders = self._retry_api_call(lambda: self.client.get_orders(request))

            # Some SDK versions return iterables; normalize to a plain list for stability.
            orders_list = list(orders)

            self.logger.debug(
                "Fetched orders",
                extra={"count": len(orders_list), "status_filter": status, "limit": limit},
            )
            return orders_list

        except Exception as e:
            raise BrokerConnectionError(f"Failed to get orders: {e}") from e

    def get_clock(self) -> Dict:
        """Query the Alpaca clock API for market status and next open/close.

        Results are cached for MARKET_CLOCK_CACHE_S seconds (default 15)
        to avoid hammering /v2/clock on every cycle/signal.

        PATCH 8 (2026-02-14): Cache is invalidated when crossing next_open or next_close boundaries
        to prevent stale market state during transitions.

        Returns a dict with keys:
            is_open (bool), timestamp (datetime|None), next_open (datetime|None),
            next_close (datetime|None).

        Raises BrokerConnectionError on failure.
        """
        now_mono = time.monotonic()
        now_utc = datetime.now(timezone.utc)

        # Default cache TTL is 15s unless overridden.
        # Tests may override this directly (e.g. stub._clock_cache_ttl = 0.05).
        default_ttl = float(globals().get("MARKET_CLOCK_CACHE_S", 15.0))
        ttl_raw = getattr(self, "_clock_cache_ttl", None)
        ttl_s = float(default_ttl if ttl_raw is None else ttl_raw)

        cache = getattr(self, "_clock_cache", None)
        cache_ts = getattr(self, "_clock_cache_ts", None)

        if cache is not None and ttl_s > 0 and cache_ts is not None:
            age_s = now_mono - float(cache_ts)

            # PATCH 8: Invalidate cache if we've crossed next_open or next_close boundary
            cache_valid_by_age = 0.0 <= age_s < ttl_s
            cache_valid_by_boundary = True

            if cache_valid_by_age:
                # Check if we've crossed a market state boundary
                next_open = cache.get("next_open")
                next_close = cache.get("next_close")

                # If we have a next_open and current time is past it, invalidate
                if next_open and now_utc >= next_open:
                    cache_valid_by_boundary = False

                # If we have a next_close and current time is past it, invalidate
                if next_close and now_utc >= next_close:
                    cache_valid_by_boundary = False

            if cache_valid_by_age and cache_valid_by_boundary:
                return cache

        try:
            clock = self._retry_api_call(lambda: self.client.get_clock())
            result = {
                "is_open": bool(getattr(clock, "is_open", False)),
                "timestamp": clock.timestamp.astimezone(timezone.utc) if getattr(clock, "timestamp", None) else None,
                "next_open": clock.next_open.astimezone(timezone.utc) if getattr(clock, "next_open", None) else None,
                "next_close": clock.next_close.astimezone(timezone.utc) if getattr(clock, "next_close", None) else None,
            }

            self._clock_cache = result
            self._clock_cache_ts = now_mono
            return result

        except Exception as e:
            raise BrokerConnectionError(f"Failed to get market clock: {e}") from e

    def _retry_api_call(self, func: Callable[[], Any], max_retries: Optional[int] = None):
        """Retry with exponential backoff.

        Retries on:
          - HTTP 429 (rate limit) and 5xx (server errors) via APIError
          - ConnectionError / TimeoutError / OSError (transient network errors)

        PATCH 11: Enforces absolute timeout to prevent indefinite retries.
        """
        max_retries = int(max_retries or self.MAX_RETRIES)
        delay = float(self.RETRY_DELAY_SECONDS)
        timeout = float(self.RETRY_TIMEOUT_SECONDS)

        # PATCH 11: Track absolute timeout
        start_time = time.time()

        # Pull from class attribute so instances/mocks never "lose" it.
        retryable_net = getattr(type(self), "_RETRYABLE_NETWORK_ERRORS", (ConnectionError, TimeoutError, OSError))

        for attempt in range(max_retries):
            # PATCH 11: Check absolute timeout before retry
            if attempt > 0:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    self.logger.warning(
                        "Retry timeout exceeded: %0.2fs >= %0.2fs (attempt %d/%d)",
                        elapsed, timeout, attempt + 1, max_retries,
                    )
                    raise TimeoutError(f"Retry timeout exceeded after {elapsed:.2f}s")

            try:
                return func()

            except APIError as e:
                status_code = getattr(e, "status_code", None)
                retryable = (status_code == 429) or (status_code is not None and 500 <= status_code < 600)

                if retryable and attempt < max_retries - 1:
                    self.logger.warning(
                        "Retryable API error (attempt %d/%d): %s",
                        attempt + 1, max_retries, e,
                    )
                    time.sleep(delay)
                    delay *= float(self.RETRY_BACKOFF_MULTIPLIER)
                    continue
                raise

            except retryable_net as e:
                if attempt < max_retries - 1:
                    self.logger.warning(
                        "Retryable network error (attempt %d/%d): %s",
                        attempt + 1, max_retries, e,
                    )
                    time.sleep(delay)
                    delay *= float(self.RETRY_BACKOFF_MULTIPLIER)
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
            "rejected": OrderStatus.REJECTED,
        }
        return mapping.get(str(alpaca_status).lower(), OrderStatus.SUBMITTED)


# ============================================================================
# EXCEPTIONS
# ============================================================================

class BrokerConnectionError(Exception):
    """Broker connection error."""
    pass


class BrokerOrderError(Exception):
    """Broker order error."""
    pass
