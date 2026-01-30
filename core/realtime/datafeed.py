"""
WebSocket real-time market data feed.
"""

import threading
from typing import Callable, Dict, List
from decimal import Decimal
from datetime import datetime

from alpaca.data.live import StockDataStream
from alpaca.data.models import Bar, Quote

from core.logging import get_logger, LogStream


class RealtimeDataFeed:
    """
    WebSocket market data feed.
    
    ARCHITECTURE:
    - Alpaca WebSocket for real-time quotes/bars
    - Async callbacks for data
    - Automatic reconnection
    - Thread-safe handlers
    """
    
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.logger = get_logger(LogStream.DATA)
        
        # Alpaca stream
        self.stream = StockDataStream(api_key, api_secret, raw_data=False)
        
        # Handlers
        self._bar_handlers: List[Callable] = []
        self._quote_handlers: List[Callable] = []
        
        # State
        self._running = False
        self._subscribed_symbols: set = set()
        
        self.logger.info("RealtimeDataFeed initialized")
    
    def subscribe_bars(self, symbols: List[str]):
        """Subscribe to bar updates."""
        for symbol in symbols:
            if symbol not in self._subscribed_symbols:
                self.stream.subscribe_bars(self._on_bar, symbol)
                self._subscribed_symbols.add(symbol)
                self.logger.info(f"Subscribed to bars: {symbol}")
    
    def subscribe_quotes(self, symbols: List[str]):
        """Subscribe to quote updates."""
        for symbol in symbols:
            self.stream.subscribe_quotes(self._on_quote, symbol)
            self.logger.info(f"Subscribed to quotes: {symbol}")
    
    def add_bar_handler(self, handler: Callable):
        """Add bar handler."""
        self._bar_handlers.append(handler)
    
    def add_quote_handler(self, handler: Callable):
        """Add quote handler."""
        self._quote_handlers.append(handler)
    
    def start(self):
        """Start WebSocket stream."""
        if self._running:
            return
        
        self._running = True
        
        # Start in thread
        thread = threading.Thread(target=self._run_stream, daemon=True)
        thread.start()
        
        self.logger.info("WebSocket stream started")
    
    def stop(self):
        """Stop WebSocket stream."""
        if not self._running:
            return
        
        self._running = False
        self.stream.stop()
        self.logger.info("WebSocket stream stopped")
    
    def _run_stream(self):
        """Run stream (blocks)."""
        try:
            self.stream.run()
        except Exception as e:
            self.logger.error(f"Stream error: {e}", exc_info=True)
            self._running = False
    
    async def _on_bar(self, bar: Bar):
        """Bar callback."""
        try:
            bar_dict = {
                "symbol": bar.symbol,
                "timestamp": bar.timestamp,
                "open": Decimal(str(bar.open)),
                "high": Decimal(str(bar.high)),
                "low": Decimal(str(bar.low)),
                "close": Decimal(str(bar.close)),
                "volume": bar.volume
            }
            
            # Call handlers
            for handler in self._bar_handlers:
                try:
                    handler(bar_dict)
                except Exception as e:
                    self.logger.error(f"Bar handler error: {e}", exc_info=True)
                    
        except Exception as e:
            self.logger.error(f"Bar processing error: {e}", exc_info=True)
    
    async def _on_quote(self, quote: Quote):
        """Quote callback."""
        try:
            quote_dict = {
                "symbol": quote.symbol,
                "timestamp": quote.timestamp,
                "bid": Decimal(str(quote.bid_price)),
                "ask": Decimal(str(quote.ask_price)),
                "bid_size": quote.bid_size,
                "ask_size": quote.ask_size
            }
            
            # Call handlers
            for handler in self._quote_handlers:
                try:
                    handler(quote_dict)
                except Exception as e:
                    self.logger.error(f"Quote handler error: {e}", exc_info=True)
                    
        except Exception as e:
            self.logger.error(f"Quote processing error: {e}", exc_info=True)
