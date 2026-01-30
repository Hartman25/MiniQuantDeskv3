"""
SQLite-backed position store with ACID guarantees.

CRITICAL PROPERTIES:
1. SQLite backend with WAL mode (concurrent reads)
2. ACID transaction guarantees
3. Fractional share support (Decimal, NOT float)
4. No in-memory cache (read from DB always)
5. Thread-safe via SQLite connection per thread
6. Schema versioning support

Based on Freqtrade's persistence layer.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict
from decimal import Decimal
from datetime import datetime
import logging

from core.logging import get_logger, LogStream
from core.time import Clock, RealTimeClock

# ============================================================================
# POSITION DATA MODEL
# ============================================================================

@dataclass
class Position:
    """
    Position data model.
    
    Uses Decimal for quantities to support fractional shares precisely.
    """
    symbol: str
    quantity: Decimal               # Can be fractional (e.g., 1.5 shares)
    entry_price: Decimal
    entry_time: datetime
    strategy: str
    order_id: str                   # Order that created this position
    
    # Optional fields
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    broker_position_id: Optional[str] = None
    
    # Metadata
    metadata: Dict = None
    
    def __post_init__(self):
        """Validate on creation."""
        if self.quantity == 0:
            raise ValueError("Position quantity cannot be zero")
        
        if self.entry_price <= 0:
            raise ValueError("Entry price must be positive")
        
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# POSITION STORE
# ============================================================================

class PositionStore:
    """
    SQLite-backed position persistence.
    
    SCHEMA:
    - positions table with TEXT fields for Decimal serialization
    - WAL mode for concurrent reads
    - Foreign key constraints disabled (simpler)
    - Auto-commit disabled (explicit transactions)
    
    THREAD SAFETY:
    - Uses thread-local connections
    - Each thread gets its own connection
    - WAL mode allows concurrent readers
    
    USAGE:
        store = PositionStore(Path("data/positions.db"))
        
        # Insert/update position
        store.upsert(position)
        
        # Get position
        pos = store.get("SPY")
        
        # Get all positions
        positions = store.get_all()
        
        # Delete position
        store.delete("SPY")
    """
    
    # Schema version for migrations
    SCHEMA_VERSION = 1
    
    # SQL statements
    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            quantity TEXT NOT NULL,
            entry_price TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            strategy TEXT NOT NULL,
            order_id TEXT NOT NULL,
            stop_loss TEXT,
            take_profit TEXT,
            current_price TEXT,
            unrealized_pnl TEXT,
            broker_position_id TEXT,
            metadata TEXT,
            updated_at TEXT NOT NULL
        )
    """
    
    CREATE_VERSION_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
    """
    
    def __init__(self, db_path: Path, clock: Optional[Clock] = None):
        """
        Initialize position store.

        Args:
            db_path: Path to SQLite database file
            clock: Optional clock for timestamps (supports backtesting).
                Defaults to RealTimeClock() for live/paper + unit tests.
        """
        self.db_path = db_path
        self.clock = clock or RealTimeClock()

        self.logger = get_logger(LogStream.POSITIONS)
        
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Thread-local storage for connections
        self._local = threading.local()
        
        # Initialize database
        self._initialize_db()
        
        self.logger.info("PositionStore initialized", extra={
            "db_path": str(self.db_path),
            "schema_version": self.SCHEMA_VERSION
        })
    
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.
        
        Each thread gets its own connection for thread safety.
        
        Returns:
            SQLite connection
        """
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.Connection(str(self.db_path), check_same_thread=False)
            
            # Enable WAL mode (write-ahead logging)
            conn.execute("PRAGMA journal_mode=WAL")
            
            # Disable auto-commit (we control transactions)
            conn.isolation_level = None
            
            # Set row factory for dict-like rows
            conn.row_factory = sqlite3.Row
            
            self._local.connection = conn
        
        return self._local.connection
    
    def _initialize_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        
        # Create tables
        conn.execute(self.CREATE_TABLE_SQL)
        conn.execute(self.CREATE_VERSION_TABLE_SQL)
        
        # Set schema version
        cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,)
            )
        
        conn.commit()
    
    def upsert(self, position: Position) -> None:
        """
        Insert or update position.
        
        Thread-safe. Uses REPLACE statement (atomic).
        
        Args:
            position: Position to store
            
        Raises:
            PositionStoreError: If database operation fails
        """
        conn = self._get_connection()
        
        try:
            conn.execute("BEGIN TRANSACTION")
            
            conn.execute("""
                REPLACE INTO positions (
                    symbol, quantity, entry_price, entry_time, strategy, order_id,
                    stop_loss, take_profit, current_price, unrealized_pnl,
                    broker_position_id, metadata, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.symbol,
                str(position.quantity),
                str(position.entry_price),
                position.entry_time.isoformat(),
                position.strategy,
                position.order_id,
                str(position.stop_loss) if position.stop_loss else None,
                str(position.take_profit) if position.take_profit else None,
                str(position.current_price) if position.current_price else None,
                str(position.unrealized_pnl) if position.unrealized_pnl else None,
                position.broker_position_id,
                str(position.metadata) if position.metadata else None,
                self.clock.now().isoformat()  # Use injected clock (backtest-safe)
            ))
            
            conn.commit()
            
            self.logger.info(f"Position upserted: {position.symbol}", extra={
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "entry_price": str(position.entry_price),
                "strategy": position.strategy
            })
            
        except Exception as e:
            conn.rollback()
            self.logger.error(
                f"Failed to upsert position: {position.symbol}",
                extra={"symbol": position.symbol, "error": str(e)},
                exc_info=True
            )
            raise PositionStoreError(f"Failed to upsert position: {e}") from e
    
    def get(self, symbol: str) -> Optional[Position]:
        """
        Get position by symbol.
        
        Args:
            symbol: Symbol to retrieve
            
        Returns:
            Position or None if not found
        """
        conn = self._get_connection()
        
        cursor = conn.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol,)
        )
        
        row = cursor.fetchone()
        if row is None:
            return None
        
        return self._row_to_position(row)
    
    def get_all(self) -> List[Position]:
        """
        Get all positions.
        
        Returns:
            List of positions (empty if none)
        """
        conn = self._get_connection()
        
        cursor = conn.execute("SELECT * FROM positions ORDER BY symbol")
        rows = cursor.fetchall()
        
        return [self._row_to_position(row) for row in rows]
    
    def delete(self, symbol: str) -> bool:
        """
        Delete position by symbol.
        
        Args:
            symbol: Symbol to delete
            
        Returns:
            True if deleted, False if not found
        """
        conn = self._get_connection()
        
        try:
            conn.execute("BEGIN TRANSACTION")
            
            cursor = conn.execute(
                "DELETE FROM positions WHERE symbol = ?",
                (symbol,)
            )
            
            deleted = cursor.rowcount > 0
            
            conn.commit()
            
            if deleted:
                self.logger.info(f"Position deleted: {symbol}", extra={
                    "symbol": symbol
                })
            
            return deleted
            
        except Exception as e:
            conn.rollback()
            self.logger.error(
                f"Failed to delete position: {symbol}",
                extra={"symbol": symbol, "error": str(e)},
                exc_info=True
            )
            raise PositionStoreError(f"Failed to delete position: {e}") from e
    
    def clear(self) -> int:
        """
        Delete all positions.
        
        Returns:
            Number of positions deleted
        """
        conn = self._get_connection()
        
        try:
            conn.execute("BEGIN TRANSACTION")
            
            cursor = conn.execute("DELETE FROM positions")
            count = cursor.rowcount
            
            conn.commit()
            
            self.logger.warning(f"All positions cleared", extra={
                "count": count
            })
            
            return count
            
        except Exception as e:
            conn.rollback()
            raise PositionStoreError(f"Failed to clear positions: {e}") from e
    
    def _row_to_position(self, row: sqlite3.Row) -> Position:
        """
        Convert database row to Position object.
        
        Args:
            row: SQLite row
            
        Returns:
            Position object
        """
        import ast
        
        return Position(
            symbol=row['symbol'],
            quantity=Decimal(row['quantity']),
            entry_price=Decimal(row['entry_price']),
            entry_time=datetime.fromisoformat(row['entry_time']),
            strategy=row['strategy'],
            order_id=row['order_id'],
            stop_loss=Decimal(row['stop_loss']) if row['stop_loss'] else None,
            take_profit=Decimal(row['take_profit']) if row['take_profit'] else None,
            current_price=Decimal(row['current_price']) if row['current_price'] else None,
            unrealized_pnl=Decimal(row['unrealized_pnl']) if row['unrealized_pnl'] else None,
            broker_position_id=row['broker_position_id'],
            metadata=ast.literal_eval(row['metadata']) if row['metadata'] else {}
        )
    
    def close(self):
        """Close database connections. Call on shutdown."""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            del self._local.connection
        
        self.logger.info("PositionStore closed")
    
    def __enter__(self):
        """Context manager support."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support."""
        self.close()


# ============================================================================
# EXCEPTIONS
# ============================================================================

class PositionStoreError(Exception):
    """Raised on position store errors."""
    pass
