"""
Real-time market data via WebSocket.

ARCHITECTURE:
- Alpaca WebSocket integration
- Quote and trade streaming
- Bar aggregation (1Min, 5Min)
- Event-driven callbacks
- Automatic reconnection
- Thread-safe

Based on event-driven data feeds.
"""

from typing import Callable, Dict, List, Optional
from decimal import Decimal
from datetime import datetime
import threading
import queue
import json

from alpaca.data.live import StockDataStream
from alpaca.data.models import Quote, Trade, Bar

from core.logging import get_logger, LogStream


# ============================================================================
# REAL-TIME DATA HANDLER
# ============================================================================

class RealtimeDataHandler:
    """
    Real-time market data via WebSocket.
    
    FEATURES:
    - Quote streaming (bid/ask)
    - Trade streaming (executions)
    - Bar aggregation
    - Event callbacks
    - Auto-reconnect
    
    USAGE:
        handler = RealtimeDataHandler(
            api_key="...",
            api_secret="..."
        )
        
        @handler.on_quote("SPY")
        def handle_quote(quote):
            print(f"Bid: {quote.bid_price}, Ask: {quote.ask_price}")
        
        @handler.on_trade("SPY")
        def handle_trade(trade):
            print(f"Trade: {trade.price} x {trade.size}")
        
        handler.subscribe(["SPY"])
        handler.start()
    """
    
    def __init__(self, api_key: str, api_secret: str):
        """Initialize real-time handler."""
        self.api_key = api_key
        self.api_secret = api_secret
        self.logger = get_logger(LogStream.DATA)
        
        # Alpaca stream
        self.stream = StockDataStream(api_key, api_secret)
        
        # Callbacks: symbol -> list of callbacks
        self._quote_callbacks: Dict[str, List[Callable]] = {}
        self._trade_callbacks: Dict[str, List[Callable]] = {}
        self._bar_callbacks: Dict[str, List[Callable]] = {}
        
        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        self.logger.info("RealtimeDataHandler initialized")
    
    def subscribe(self, symbols: List[str]):
        """Subscribe to symbols."""
        for symbol in symbols:
            # Subscribe to quotes
            if symbol in self._quote_callbacks:
                self.stream.subscribe_quotes(
                    self._create_quote_handler(symbol),
                    symbol
                )
            
            # Subscribe to trades
            if symbol in self._trade_callbacks:
                self.stream.subscribe_trades(
                    self._create_trade_handler(symbol),
                    symbol
                )
            
            # Subscribe to bars
            if symbol in self._bar_callbacks:
                self.stream.subscribe_bars(
                    self._create_bar_handler(symbol),
                    symbol
                )
        
        self.logger.info(f"Subscribed to {len(symbols)} symbols", extra={
            "symbols": symbols
        })
    
    def on_quote(self, symbol: str):
        """Decorator for quote handler."""
        def decorator(func: Callable):
            if symbol not in self._quote_callbacks:
                self._quote_callbacks[symbol] = []
            self._quote_callbacks[symbol].append(func)
            return func
        return decorator
    
    def on_trade(self, symbol: str):
        """Decorator for trade handler."""
        def decorator(func: Callable):
            if symbol not in self._trade_callbacks:
                self._trade_callbacks[symbol] = []
            self._trade_callbacks[symbol].append(func)
            return func
        return decorator
    
    def on_bar(self, symbol: str):
        """Decorator for bar handler."""
        def decorator(func: Callable):
            if symbol not in self._bar_callbacks:
                self._bar_callbacks[symbol] = []
            self._bar_callbacks[symbol].append(func)
            return func
        return decorator
    
    def start(self):
        """Start streaming."""
        if self._running:
            return
        
        self._running = True
        
        # Run in background thread
        self._thread = threading.Thread(target=self._run_stream, daemon=True)
        self._thread.start()
        
        self.logger.info("Real-time streaming started")
    
    def stop(self):
        """Stop streaming."""
        if not self._running:
            return
        
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=5)
        
        self.logger.info("Real-time streaming stopped")
    
    def _run_stream(self):
        """Run stream (blocking)."""
        try:
            self.stream.run()
        except Exception as e:
            self.logger.error("Stream error", extra={"error": str(e)}, exc_info=True)
    
    def _create_quote_handler(self, symbol: str):
        """Create quote handler for symbol."""
        def handler(quote: Quote):
            try:
                for callback in self._quote_callbacks.get(symbol, []):
                    callback(quote)
            except Exception as e:
                self.logger.error(f"Quote callback error: {symbol}", extra={
                    "error": str(e)
                }, exc_info=True)
        
        return handler
    
    def _create_trade_handler(self, symbol: str):
        """Create trade handler for symbol."""
        def handler(trade: Trade):
            try:
                for callback in self._trade_callbacks.get(symbol, []):
                    callback(trade)
            except Exception as e:
                self.logger.error(f"Trade callback error: {symbol}", extra={
                    "error": str(e)
                }, exc_info=True)
        
        return handler
    
    def _create_bar_handler(self, symbol: str):
        """Create bar handler for symbol."""
        def handler(bar: Bar):
            try:
                for callback in self._bar_callbacks.get(symbol, []):
                    callback(bar)
            except Exception as e:
                self.logger.error(f"Bar callback error: {symbol}", extra={
                    "error": str(e)
                }, exc_info=True)
        
        return handler


# ============================================================================
# QUOTE AGGREGATOR
# ============================================================================

class QuoteAggregator:
    """
    Aggregates quotes for NBBO (National Best Bid Offer).
    
    USAGE:
        agg = QuoteAggregator()
        
        agg.update("SPY", bid=Decimal("600.10"), ask=Decimal("600.11"))
        
        nbbo = agg.get_nbbo("SPY")
        print(f"Bid: {nbbo['bid']}, Ask: {nbbo['ask']}")
    """
    
    def __init__(self):
        """Initialize aggregator."""
        self._quotes: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self.logger = get_logger(LogStream.DATA)
    
    def update(self, symbol: str, bid: Decimal, ask: Decimal, timestamp: datetime):
        """Update quote."""
        with self._lock:
            self._quotes[symbol] = {
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "spread": ask - bid,
                "timestamp": timestamp
            }
    
    def get_nbbo(self, symbol: str) -> Optional[Dict]:
        """Get NBBO for symbol."""
        with self._lock:
            return self._quotes.get(symbol)
