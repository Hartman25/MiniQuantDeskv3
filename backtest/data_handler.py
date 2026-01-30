"""
Historical data handler for backtesting.

LEAN COMPATIBILITY:
Similar to QuantConnect's SubscriptionDataReader.

ARCHITECTURE:
- Load historical bars from parquet/CSV
- Iterate chronologically
- Support multiple symbols
- Support multiple timeframes
- Data alignment and synchronization

Multi-asset ready.
"""

from typing import Dict, List, Optional, Iterator
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta
import pandas as pd

from backtest.fill_models import AssetClass
from core.logging import get_logger, LogStream


# ============================================================================
# HISTORICAL DATA HANDLER
# ============================================================================

class HistoricalDataHandler:
    """
    Historical data manager for backtesting.
    
    RESPONSIBILITIES:
    - Load historical data from disk
    - Provide chronological iteration
    - Support multiple symbols
    - Handle data gaps
    - Normalize timestamps
    
    USAGE:
        handler = HistoricalDataHandler(data_dir="data/")
        handler.load_symbol("SPY", start_date, end_date)
        
        for timestamp, bars in handler:
            # bars = {"SPY": {open, high, low, close, volume}}
            process_bars(timestamp, bars)
    """
    
    def __init__(
        self,
        data_dir: Path,
        asset_class: AssetClass = AssetClass.EQUITY
    ):
        """
        Initialize data handler.
        
        Args:
            data_dir: Directory containing historical data
            asset_class: Asset class for this handler
        """
        self.data_dir = Path(data_dir)
        self.asset_class = asset_class
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Loaded data: {symbol: DataFrame}
        self.data: Dict[str, pd.DataFrame] = {}
        
        # Current position in iteration
        self.current_index = 0
        self.timestamps: List[datetime] = []
        
        self.logger.info("HistoricalDataHandler initialized", extra={
            "data_dir": str(data_dir),
            "asset_class": asset_class.value
        })
    
    def load_symbol(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        resolution: str = "1Day"
    ):
        """
        Load historical data for symbol.
        
        Args:
            symbol: Symbol to load
            start_date: Start date
            end_date: End date
            resolution: Bar resolution (1Day, 1Hour, etc)
        """
        # Construct file path
        # Assume parquet format: data/SPY_1Day.parquet
        file_path = self.data_dir / f"{symbol}_{resolution}.parquet"
        
        if not file_path.exists():
            # Try CSV format
            file_path = self.data_dir / f"{symbol}_{resolution}.csv"
        
        if not file_path.exists():
            self.logger.error(f"Data file not found: {file_path}")
            raise FileNotFoundError(f"No data file for {symbol}")
        
        # Load data
        if file_path.suffix == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, parse_dates=['timestamp'])
        
        # Filter by date range
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
        
        # Sort by timestamp
        df = df.sort_values('timestamp')
        df = df.reset_index(drop=True)
        
        # Store
        self.data[symbol] = df
        
        self.logger.info(f"Loaded {len(df)} bars for {symbol}", extra={
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "bars": len(df)
        })
        
        # Update timestamps (union of all symbol timestamps)
        self._update_timestamps()
    
    def _update_timestamps(self):
        """Update unified timestamp index."""
        all_timestamps = set()
        
        for symbol, df in self.data.items():
            all_timestamps.update(df['timestamp'].tolist())
        
        self.timestamps = sorted(list(all_timestamps))
        self.current_index = 0
        
        self.logger.info(f"Updated timestamps: {len(self.timestamps)} total")
    
    def __iter__(self) -> Iterator[tuple]:
        """Iterate chronologically through data."""
        self.current_index = 0
        return self
    
    def __next__(self) -> tuple:
        """
        Get next timestamp and bars.
        
        Returns:
            (timestamp, {symbol: bar_dict})
        """
        if self.current_index >= len(self.timestamps):
            raise StopIteration
        
        timestamp = self.timestamps[self.current_index]
        bars = {}
        
        # Get bar for each symbol at this timestamp
        for symbol, df in self.data.items():
            # Find bar at this timestamp
            bar_row = df[df['timestamp'] == timestamp]
            
            if not bar_row.empty:
                bar_row = bar_row.iloc[0]
                bars[symbol] = {
                    'open': float(bar_row['open']),
                    'high': float(bar_row['high']),
                    'low': float(bar_row['low']),
                    'close': float(bar_row['close']),
                    'volume': float(bar_row['volume']) if 'volume' in bar_row else 0
                }
        
        self.current_index += 1
        return (timestamp, bars)
    
    def get_bar(self, symbol: str, timestamp: datetime) -> Optional[dict]:
        """
        Get specific bar.
        
        Args:
            symbol: Symbol
            timestamp: Timestamp
            
        Returns:
            Bar dict or None
        """
        if symbol not in self.data:
            return None
        
        df = self.data[symbol]
        bar_row = df[df['timestamp'] == timestamp]
        
        if bar_row.empty:
            return None
        
        bar_row = bar_row.iloc[0]
        return {
            'open': float(bar_row['open']),
            'high': float(bar_row['high']),
            'low': float(bar_row['low']),
            'close': float(bar_row['close']),
            'volume': float(bar_row['volume']) if 'volume' in bar_row else 0
        }
    
    def get_latest_price(self, symbol: str) -> Optional[Decimal]:
        """
        Get latest known price for symbol.
        
        Args:
            symbol: Symbol
            
        Returns:
            Latest close price or None
        """
        if symbol not in self.data:
            return None
        
        if self.current_index == 0:
            return None
        
        # Get last processed timestamp
        last_timestamp = self.timestamps[self.current_index - 1]
        bar = self.get_bar(symbol, last_timestamp)
        
        if bar:
            return Decimal(str(bar['close']))
        
        return None
