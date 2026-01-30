"""
Persistent Risk Limits Tracker - SQLite-backed daily limits.

CRITICAL RULES:
1. Daily loss limit persists across restarts
2. P&L tracked in SQLite (NOT in-memory)
3. Reset happens ONLY on new trading day
4. Thread-safe (lock on all mutations)
5. Fail-safe: If DB unavailable, BLOCK all trading

Prevents circumventing loss limits via restart.
Based on QuantConnect's daily loss limit pattern.
"""

import sqlite3
from datetime import datetime, timezone, date
from decimal import Decimal
from threading import Lock
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# PERSISTENT LIMITS TRACKER
# ============================================================================

class PersistentLimitsTracker:
    """
    Tracks daily P&L and risk limits with SQLite persistence.
    
    INVARIANTS:
    - Daily loss limit CANNOT be exceeded
    - Restart does NOT reset limits
    - Only new calendar day resets limits
    - All mutations are atomic (SQLite transaction)
    
    Usage:
        tracker = PersistentLimitsTracker(
            db_path="limits.db",
            daily_loss_limit=Decimal("100.00")
        )
        
        # Check before trading
        if tracker.is_daily_loss_limit_breached():
            logger.error("Trading halted: loss limit breached")
            return
        
        # Record trade
        tracker.record_realized_pnl(pnl=Decimal("-25.50"))
        
        # Daily reset (called by scheduler at market open)
        tracker.reset_daily_limits()
    """
    
    def __init__(
        self,
        db_path: str,
        daily_loss_limit: Decimal,
        max_position_size: Optional[Decimal] = None,
        max_notional_exposure: Optional[Decimal] = None
    ):
        """
        Initialize limits tracker.
        
        Args:
            db_path: SQLite database path
            daily_loss_limit: Maximum $ loss per day (positive value)
            max_position_size: Maximum shares per position
            max_notional_exposure: Maximum $ exposure across all positions
        """
        self.db_path = Path(db_path)
        self.daily_loss_limit = abs(daily_loss_limit)
        self.max_position_size = max_position_size
        self.max_notional_exposure = max_notional_exposure
        
        self._lock = Lock()
        
        # Initialize database
        self._init_db()
        
        # Auto-reset if new day
        self._check_and_reset_if_new_day()
        
        logger.info(
            f"PersistentLimitsTracker initialized "
            f"(daily_loss_limit=${self.daily_loss_limit}, "
            f"max_position_size={self.max_position_size}, "
            f"max_notional=${self.max_notional_exposure})"
        )
    
    # ========================================================================
    # DATABASE INITIALIZATION
    # ========================================================================
    
    def _init_db(self) -> None:
        """Initialize SQLite schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    trading_date TEXT PRIMARY KEY,
                    realized_pnl TEXT NOT NULL,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    loss_limit_breached INTEGER NOT NULL DEFAULT 0,
                    last_updated_utc TEXT NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS limit_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Store limits in config table
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO limit_config (key, value, updated_at)
                VALUES (?, ?, ?)
            """, ('daily_loss_limit', str(self.daily_loss_limit), now))
            
            conn.commit()
            
        logger.info(f"Limits database initialized: {self.db_path}")
    
    # ========================================================================
    # DAILY LIMIT CHECKS
    # ========================================================================
    
    def is_daily_loss_limit_breached(self) -> bool:
        """
        Check if daily loss limit has been exceeded.
        
        Returns:
            True if limit breached, trading should STOP
        """
        with self._lock:
            current_pnl = self.get_daily_realized_pnl()
            is_breached = current_pnl <= -self.daily_loss_limit
            
            if is_breached:
                logger.error(
                    f"DAILY LOSS LIMIT BREACHED: "
                    f"PnL=${current_pnl} <= limit=-${self.daily_loss_limit}"
                )
            
            return is_breached
    
    def get_daily_realized_pnl(self) -> Decimal:
        """
        Get today's realized P&L.
        
        Returns:
            Cumulative P&L for today (negative = loss)
        """
        today = date.today().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT realized_pnl FROM daily_pnl
                WHERE trading_date = ?
            """, (today,))
            
            row = cursor.fetchone()
            if row:
                return Decimal(row[0])
            else:
                return Decimal('0')
    
    def get_remaining_loss_buffer(self) -> Decimal:
        """
        Get remaining loss buffer before limit breach.
        
        Returns:
            Positive value = buffer remaining
            Zero or negative = limit breached
        """
        current_pnl = self.get_daily_realized_pnl()
        return self.daily_loss_limit + current_pnl  # current_pnl is negative for loss
    
    # ========================================================================
    # P&L RECORDING
    # ========================================================================
    
    def record_realized_pnl(self, pnl: Decimal) -> None:
        """
        Record realized P&L from closed position.
        
        Args:
            pnl: Realized profit/loss (negative = loss)
        """
        with self._lock:
            today = date.today().isoformat()
            now = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                # Get current P&L
                cursor = conn.execute("""
                    SELECT realized_pnl, trade_count FROM daily_pnl
                    WHERE trading_date = ?
                """, (today,))
                
                row = cursor.fetchone()
                
                if row:
                    # Update existing record
                    current_pnl = Decimal(row[0])
                    trade_count = row[1]
                    
                    new_pnl = current_pnl + pnl
                    new_count = trade_count + 1
                    
                    # Check if limit breached
                    breached = 1 if new_pnl <= -self.daily_loss_limit else 0
                    
                    conn.execute("""
                        UPDATE daily_pnl
                        SET realized_pnl = ?,
                            trade_count = ?,
                            loss_limit_breached = ?,
                            last_updated_utc = ?
                        WHERE trading_date = ?
                    """, (str(new_pnl), new_count, breached, now, today))
                else:
                    # Create new record
                    breached = 1 if pnl <= -self.daily_loss_limit else 0
                    
                    conn.execute("""
                        INSERT INTO daily_pnl (
                            trading_date, realized_pnl, trade_count,
                            loss_limit_breached, last_updated_utc
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (today, str(pnl), 1, breached, now))
                
                conn.commit()
            
            logger.info(
                f"[LIMIT_TRACKER] Recorded P&L: ${pnl} "
                f"(daily_total=${self.get_daily_realized_pnl()})"
            )
            
            # Check and log if limit breached
            if self.is_daily_loss_limit_breached():
                logger.error(
                    f"[LIMIT_BREACH] Daily loss limit exceeded! "
                    f"Trading should be HALTED."
                )
    
    # ========================================================================
    # DAILY RESET
    # ========================================================================
    
    def reset_daily_limits(self) -> None:
        """
        Reset daily limits for new trading day.
        
        ONLY call this at start of new trading day.
        Typically triggered by scheduler at market open.
        """
        with self._lock:
            today = date.today().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                # Check if today already has a record
                cursor = conn.execute("""
                    SELECT trading_date FROM daily_pnl
                    WHERE trading_date = ?
                """, (today,))
                
                if cursor.fetchone():
                    logger.warning(
                        f"Daily limits already exist for {today}, skipping reset"
                    )
                    return
                
                # Create fresh record for today
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO daily_pnl (
                        trading_date, realized_pnl, trade_count,
                        loss_limit_breached, last_updated_utc
                    ) VALUES (?, ?, ?, ?, ?)
                """, (today, '0', 0, 0, now))
                
                conn.commit()
            
            logger.info(f"[LIMIT_RESET] Daily limits reset for {today}")
    
    def _check_and_reset_if_new_day(self) -> None:
        """Auto-reset if starting on new calendar day."""
        today = date.today().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT MAX(trading_date) FROM daily_pnl
            """)
            
            row = cursor.fetchone()
            last_date = row[0] if row and row[0] else None
            
            if last_date and last_date != today:
                logger.info(
                    f"New trading day detected (last={last_date}, today={today}), "
                    f"resetting limits"
                )
                self.reset_daily_limits()
    
    # ========================================================================
    # POSITION SIZE CHECKS
    # ========================================================================
    
    def is_position_size_allowed(self, quantity: Decimal) -> bool:
        """
        Check if position size is within limits.
        
        Args:
            quantity: Position size (shares)
            
        Returns:
            True if allowed, False if exceeds limit
        """
        if self.max_position_size is None:
            return True
        
        is_allowed = abs(quantity) <= self.max_position_size
        
        if not is_allowed:
            logger.warning(
                f"Position size {quantity} exceeds limit {self.max_position_size}"
            )
        
        return is_allowed
    
    def is_notional_exposure_allowed(
        self,
        current_exposure: Decimal,
        additional_exposure: Decimal
    ) -> bool:
        """
        Check if adding position would exceed notional exposure limit.
        
        Args:
            current_exposure: Current total notional exposure
            additional_exposure: New position notional value
            
        Returns:
            True if allowed, False if would exceed limit
        """
        if self.max_notional_exposure is None:
            return True
        
        total_exposure = current_exposure + additional_exposure
        is_allowed = total_exposure <= self.max_notional_exposure
        
        if not is_allowed:
            logger.warning(
                f"Notional exposure ${total_exposure} would exceed limit "
                f"${self.max_notional_exposure}"
            )
        
        return is_allowed
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_stats(self) -> dict:
        """Get current limits and statistics."""
        return {
            'daily_loss_limit': str(self.daily_loss_limit),
            'daily_realized_pnl': str(self.get_daily_realized_pnl()),
            'remaining_buffer': str(self.get_remaining_loss_buffer()),
            'limit_breached': self.is_daily_loss_limit_breached(),
            'max_position_size': str(self.max_position_size) if self.max_position_size else None,
            'max_notional_exposure': str(self.max_notional_exposure) if self.max_notional_exposure else None
        }
