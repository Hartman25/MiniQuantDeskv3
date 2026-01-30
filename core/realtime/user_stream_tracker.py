"""
User Stream Tracker - Real-time WebSocket order and account updates.

Connects to Alpaca WebSocket trading stream for instant notifications:
- Trade updates (fills, partial fills, cancels)
- Account updates (balance, buying power)

Eliminates polling delays and missed updates.

Pattern stolen from: Hummingbot user_stream_tracker.py
"""

import asyncio
import json
from typing import Optional, Dict, Callable, List
from datetime import datetime, timezone
from enum import Enum
import logging

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class StreamEventType(Enum):
    """WebSocket event types"""
    TRADE_UPDATE = "trade_updates"
    ACCOUNT_UPDATE = "account_updates"
    LISTENING = "listening"
    AUTHORIZED = "authorized"
    ERROR = "error"


class UserStreamTracker:
    """
    Tracks user-specific WebSocket stream from Alpaca.
    
    Provides real-time updates for:
    - Order fills (instant notification vs polling)
    - Partial fills (don't miss them)
    - Order cancellations
    - Order rejections
    - Account balance changes
    - Buying power updates
    
    Usage:
        tracker = UserStreamTracker(
            api_key=config['alpaca']['api_key'],
            api_secret=config['alpaca']['api_secret'],
            is_paper=True
        )
        
        # Register handlers
        tracker.on_trade_update(handle_fill)
        tracker.on_account_update(handle_balance)
        
        # Start tracking
        await tracker.start()
        
        # Later: stop
        await tracker.stop()
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        is_paper: bool = True
    ):
        """
        Args:
            api_key: Alpaca API key
            api_secret: Alpaca API secret
            is_paper: True for paper trading, False for live
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._is_paper = is_paper
        
        # WebSocket URL
        if is_paper:
            self._ws_url = "wss://paper-api.alpaca.markets/stream"
        else:
            self._ws_url = "wss://api.alpaca.markets/stream"
        
        # Connection state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._is_running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # Event handlers
        self._trade_handlers: List[Callable] = []
        self._account_handlers: List[Callable] = []
        self._error_handlers: List[Callable] = []
        
        # Statistics
        self._total_trade_updates = 0
        self._total_account_updates = 0
        self._connection_count = 0
        self._last_message_time: Optional[datetime] = None
        
        logger.info(
            f"UserStreamTracker initialized (paper={is_paper})",
            extra={'ws_url': self._ws_url}
        )
    
    def on_trade_update(self, handler: Callable):
        """
        Register trade update handler.
        
        Handler signature: async def handle_trade(update: dict)
        
        Update contains:
        - event: 'new', 'fill', 'partial_fill', 'canceled', 'rejected'
        - order: Full order object
        - timestamp: Event timestamp
        """
        self._trade_handlers.append(handler)
        logger.info(f"Registered trade update handler: {handler.__name__}")
    
    def on_account_update(self, handler: Callable):
        """
        Register account update handler.
        
        Handler signature: async def handle_account(update: dict)
        
        Update contains:
        - cash: Current cash balance
        - buying_power: Available buying power
        - equity: Total equity
        """
        self._account_handlers.append(handler)
        logger.info(f"Registered account update handler: {handler.__name__}")
    
    def on_error(self, handler: Callable):
        """
        Register error handler.
        
        Handler signature: async def handle_error(error: dict)
        """
        self._error_handlers.append(handler)
        logger.info(f"Registered error handler: {handler.__name__}")
    
    async def start(self):
        """Start tracking user stream"""
        if self._is_running:
            logger.warning("UserStreamTracker already running")
            return
        
        self._is_running = True
        logger.info("Starting UserStreamTracker...")
        
        # Start connection
        await self._connect()
        
        # Start receive loop
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("UserStreamTracker started")
    
    async def stop(self):
        """Stop tracking user stream"""
        if not self._is_running:
            return
        
        logger.info("Stopping UserStreamTracker...")
        self._is_running = False
        
        # Cancel tasks
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Close connection
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("UserStreamTracker stopped")
    
    async def _connect(self):
        """Establish WebSocket connection"""
        try:
            logger.info(f"Connecting to {self._ws_url}...")
            
            self._ws = await websockets.connect(
                self._ws_url,
                ping_interval=20,  # Keep-alive ping every 20s
                ping_timeout=10
            )
            
            self._connection_count += 1
            
            logger.info(
                f"WebSocket connected (connection #{self._connection_count})"
            )
            
            # Authenticate
            await self._authenticate()
            
            # Subscribe to streams
            await self._subscribe()
            
        except Exception as e:
            logger.error(
                f"Failed to connect to user stream: {e}",
                exc_info=True
            )
            
            # Schedule reconnect
            if self._is_running:
                logger.info("Scheduling reconnect in 5 seconds...")
                await asyncio.sleep(5)
                if self._is_running:
                    await self._connect()
    
    async def _authenticate(self):
        """Authenticate with API credentials"""
        auth_msg = {
            "action": "auth",
            "key": self._api_key,
            "secret": self._api_secret
        }
        
        await self._ws.send(json.dumps(auth_msg))
        logger.info("Authentication message sent")
        
        # Wait for auth response
        response = await self._ws.recv()
        response_data = json.loads(response)
        
        if response_data[0].get('T') == 'success' and response_data[0].get('msg') == 'authenticated':
            logger.info("Successfully authenticated")
        else:
            logger.error(f"Authentication failed: {response_data}")
            raise Exception("Authentication failed")
    
    async def _subscribe(self):
        """Subscribe to trade and account updates"""
        subscribe_msg = {
            "action": "listen",
            "data": {
                "streams": ["trade_updates", "account_updates"]
            }
        }
        
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to trade_updates and account_updates")
    
    async def _receive_loop(self):
        """Main receive loop"""
        while self._is_running:
            try:
                if not self._ws:
                    logger.warning("WebSocket not connected, reconnecting...")
                    await self._connect()
                    continue
                
                # Receive message
                message = await self._ws.recv()
                self._last_message_time = datetime.now(timezone.utc)
                
                # Parse message
                data = json.loads(message)
                
                # Handle message
                await self._handle_message(data)
                
            except ConnectionClosed as e:
                logger.warning(
                    f"WebSocket connection closed: {e}",
                    extra={'code': e.code, 'reason': e.reason}
                )
                
                if self._is_running:
                    logger.info("Attempting to reconnect...")
                    await asyncio.sleep(5)
                    await self._connect()
                else:
                    break
                    
            except asyncio.CancelledError:
                logger.info("Receive loop cancelled")
                break
                
            except Exception as e:
                logger.error(
                    f"Error in receive loop: {e}",
                    exc_info=True
                )
                
                # Don't spam reconnects on persistent errors
                await asyncio.sleep(5)
    
    async def _handle_message(self, data: List[dict]):
        """
        Handle incoming WebSocket message.
        
        Alpaca sends messages as array of objects.
        """
        for item in data:
            msg_type = item.get('stream')
            
            if msg_type == 'trade_updates':
                await self._handle_trade_update(item.get('data', {}))
                
            elif msg_type == 'account_updates':
                await self._handle_account_update(item.get('data', {}))
                
            elif msg_type == 'listening':
                logger.info(f"Listening to streams: {item.get('data', {}).get('streams', [])}")
                
            elif msg_type == 'authorization':
                status = item.get('data', {}).get('status')
                if status == 'authorized':
                    logger.info("Stream authorized")
                else:
                    logger.error(f"Authorization status: {status}")
                    
            else:
                logger.debug(f"Unknown message type: {msg_type}")
    
    async def _handle_trade_update(self, data: dict):
        """Handle trade update"""
        self._total_trade_updates += 1
        
        event_type = data.get('event')
        order = data.get('order', {})
        
        logger.info(
            f"Trade update: {event_type}",
            extra={
                'event': event_type,
                'order_id': order.get('client_order_id'),
                'symbol': order.get('symbol'),
                'status': order.get('status'),
                'filled_qty': order.get('filled_qty')
            }
        )
        
        # Call handlers
        for handler in self._trade_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(
                    f"Error in trade update handler: {e}",
                    exc_info=True
                )
    
    async def _handle_account_update(self, data: dict):
        """Handle account update"""
        self._total_account_updates += 1
        
        logger.info(
            "Account update",
            extra={
                'cash': data.get('cash'),
                'buying_power': data.get('buying_power'),
                'equity': data.get('equity')
            }
        )
        
        # Call handlers
        for handler in self._account_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(
                    f"Error in account update handler: {e}",
                    exc_info=True
                )
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._ws is not None and self._ws.open
    
    def get_stats(self) -> dict:
        """Get connection statistics"""
        return {
            'is_running': self._is_running,
            'is_connected': self.is_connected(),
            'total_trade_updates': self._total_trade_updates,
            'total_account_updates': self._total_account_updates,
            'connection_count': self._connection_count,
            'last_message_time': (
                self._last_message_time.isoformat()
                if self._last_message_time
                else None
            ),
            'ws_url': self._ws_url
        }
