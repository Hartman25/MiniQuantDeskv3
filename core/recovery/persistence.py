"""
State persistence and recovery system.

ARCHITECTURE:
- Periodic state snapshots to disk
- Atomic writes with backup rotation
- Fast recovery on startup
- State validation and integrity checks
- Automatic corruption detection

STATE COMPONENTS:
- Open positions
- Pending orders
- Account state
- Risk limits state
- Trading session metadata

DESIGN PRINCIPLES:
- Write-ahead logging (WAL) pattern
- Atomic file operations
- Checksum validation
- Backup rotation (keep last 5)
- Fast writes (<5ms)

Based on LEAN's state management and Freqtrade's persistence patterns.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import hashlib
import shutil
from threading import Lock

from core.logging import get_logger, LogStream


# ============================================================================
# STATE SNAPSHOT DEFINITIONS
# ============================================================================

@dataclass
class PositionSnapshot:
    """Snapshot of a single position."""
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    entry_time: datetime
    unrealized_pnl: Decimal
    side: str  # LONG or SHORT
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "avg_price": str(self.avg_price),
            "entry_time": self.entry_time.isoformat(),
            "unrealized_pnl": str(self.unrealized_pnl),
            "side": self.side
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PositionSnapshot':
        """Create from dictionary."""
        return cls(
            symbol=data["symbol"],
            quantity=Decimal(data["quantity"]),
            avg_price=Decimal(data["avg_price"]),
            entry_time=datetime.fromisoformat(data["entry_time"]),
            unrealized_pnl=Decimal(data["unrealized_pnl"]),
            side=data["side"]
        )


@dataclass
class OrderSnapshot:
    """Snapshot of a pending order."""
    order_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Optional[Decimal]
    status: str
    submitted_at: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "order_type": self.order_type,
            "limit_price": str(self.limit_price) if self.limit_price else None,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'OrderSnapshot':
        """Create from dictionary."""
        return cls(
            order_id=data["order_id"],
            broker_order_id=data["broker_order_id"],
            symbol=data["symbol"],
            side=data["side"],
            quantity=Decimal(data["quantity"]),
            order_type=data["order_type"],
            limit_price=Decimal(data["limit_price"]) if data.get("limit_price") else None,
            status=data["status"],
            submitted_at=datetime.fromisoformat(data["submitted_at"])
        )


@dataclass
class AccountSnapshot:
    """Snapshot of account state."""
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    portfolio_value: Decimal
    daily_pnl: Decimal
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "equity": str(self.equity),
            "cash": str(self.cash),
            "buying_power": str(self.buying_power),
            "portfolio_value": str(self.portfolio_value),
            "daily_pnl": str(self.daily_pnl),
            "timestamp": self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AccountSnapshot':
        """Create from dictionary."""
        return cls(
            equity=Decimal(data["equity"]),
            cash=Decimal(data["cash"]),
            buying_power=Decimal(data["buying_power"]),
            portfolio_value=Decimal(data["portfolio_value"]),
            daily_pnl=Decimal(data["daily_pnl"]),
            timestamp=datetime.fromisoformat(data["timestamp"])
        )


@dataclass
class SystemStateSnapshot:
    """Complete system state snapshot."""
    version: str = "1.0"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Trading state
    positions: List[PositionSnapshot] = field(default_factory=list)
    pending_orders: List[OrderSnapshot] = field(default_factory=list)
    account: Optional[AccountSnapshot] = None
    
    # Session metadata
    session_start: Optional[datetime] = None
    trades_today: int = 0
    total_pnl_today: Decimal = Decimal("0")
    
    # Risk state
    daily_loss_limit_remaining: Decimal = Decimal("0")
    max_position_count: int = 10
    current_position_count: int = 0
    
    # Integrity
    checksum: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        data = {
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "positions": [p.to_dict() for p in self.positions],
            "pending_orders": [o.to_dict() for o in self.pending_orders],
            "account": self.account.to_dict() if self.account else None,
            "session_start": self.session_start.isoformat() if self.session_start else None,
            "trades_today": self.trades_today,
            "total_pnl_today": str(self.total_pnl_today),
            "daily_loss_limit_remaining": str(self.daily_loss_limit_remaining),
            "max_position_count": self.max_position_count,
            "current_position_count": self.current_position_count
        }
        
        # Calculate checksum (exclude checksum field itself)
        self.checksum = self._calculate_checksum(data)
        data["checksum"] = self.checksum
        
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'SystemStateSnapshot':
        """Create from dictionary."""
        # Extract checksum
        stored_checksum = data.pop("checksum", None)
        
        # Verify checksum
        calculated_checksum = cls._calculate_checksum(data)
        if stored_checksum != calculated_checksum:
            raise ValueError(f"Checksum mismatch: stored={stored_checksum}, calculated={calculated_checksum}")
        
        snapshot = cls(
            version=data["version"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            positions=[PositionSnapshot.from_dict(p) for p in data["positions"]],
            pending_orders=[OrderSnapshot.from_dict(o) for o in data["pending_orders"]],
            account=AccountSnapshot.from_dict(data["account"]) if data.get("account") else None,
            session_start=datetime.fromisoformat(data["session_start"]) if data.get("session_start") else None,
            trades_today=data["trades_today"],
            total_pnl_today=Decimal(data["total_pnl_today"]),
            daily_loss_limit_remaining=Decimal(data["daily_loss_limit_remaining"]),
            max_position_count=data["max_position_count"],
            current_position_count=data["current_position_count"],
            checksum=stored_checksum
        )
        
        return snapshot
    
    @staticmethod
    def _calculate_checksum(data: Dict) -> str:
        """Calculate SHA256 checksum of state data."""
        # Convert to deterministic JSON string
        json_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()


# ============================================================================
# STATE PERSISTENCE MANAGER
# ============================================================================

class StatePersistence:
    """
    State persistence and recovery manager.
    
    RESPONSIBILITIES:
    - Periodic state snapshots to disk
    - Atomic file operations (write-ahead logging)
    - Backup rotation (keep last 5)
    - Checksum validation
    - Fast recovery on startup
    
    DESIGN:
    - Write to temporary file first
    - Atomic rename to target file
    - Keep backup of previous state
    - Rotate backups (max 5)
    - Validate on read
    
    FILE STRUCTURE:
    state/
      ├─ current_state.json      (active state)
      ├─ current_state.json.bak  (previous state)
      └─ backups/
         ├─ state_20250123_120000.json
         ├─ state_20250123_120100.json
         └─ ...
    
    USAGE:
        persistence = StatePersistence(
            state_dir=Path("state"),
            backup_count=5
        )
        
        # Save state
        snapshot = SystemStateSnapshot(...)
        persistence.save_state(snapshot)
        
        # Load state on startup
        snapshot = persistence.load_latest_state()
        if snapshot:
            # Restore positions, orders, etc
            restore_from_snapshot(snapshot)
    """
    
    def __init__(
        self,
        state_dir: Path = Path("state"),
        backup_count: int = 5,
        auto_backup: bool = True
    ):
        """
        Initialize state persistence.
        
        Args:
            state_dir: Directory for state files
            backup_count: Number of backups to keep
            auto_backup: Automatically backup on save
        """
        self.state_dir = state_dir
        self.backup_count = backup_count
        self.auto_backup = auto_backup
        
        self.logger = get_logger(LogStream.SYSTEM)
        
        # File paths
        self.current_state_file = state_dir / "current_state.json"
        self.backup_state_file = state_dir / "current_state.json.bak"
        self.backup_dir = state_dir / "backups"
        
        # Lock for thread safety
        self._write_lock = Lock()
        
        # Create directories
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger.info("StatePersistence initialized", extra={
            "state_dir": str(state_dir),
            "backup_count": backup_count
        })
    
    # ========================================================================
    # SAVE OPERATIONS
    # ========================================================================
    
    def save_state(self, snapshot: SystemStateSnapshot) -> bool:
        """
        Save state snapshot to disk.
        
        Uses atomic write pattern:
        1. Write to temporary file
        2. Backup current state
        3. Atomic rename to current state
        4. Rotate backups
        
        Args:
            snapshot: State snapshot to save
            
        Returns:
            True if successful, False otherwise
        """
        with self._write_lock:
            try:
                # Convert to JSON
                state_data = snapshot.to_dict()
                json_str = json.dumps(state_data, indent=2)
                
                # Write to temporary file
                temp_file = self.current_state_file.with_suffix(".tmp")
                temp_file.write_text(json_str, encoding='utf-8')
                
                # Backup current state (if exists)
                if self.auto_backup and self.current_state_file.exists():
                    shutil.copy2(self.current_state_file, self.backup_state_file)
                
                # Atomic rename
                temp_file.replace(self.current_state_file)
                
                # Create timestamped backup
                if self.auto_backup:
                    self._create_timestamped_backup(snapshot.timestamp)
                
                # Rotate old backups
                self._rotate_backups()
                
                self.logger.debug("State saved", extra={
                    "timestamp": snapshot.timestamp.isoformat(),
                    "positions": len(snapshot.positions),
                    "orders": len(snapshot.pending_orders)
                })
                
                return True
                
            except Exception as e:
                self.logger.error(
                    "Failed to save state",
                    extra={"error": str(e)},
                    exc_info=True
                )
                return False
    
    def _create_timestamped_backup(self, timestamp: datetime):
        """Create timestamped backup in backup directory."""
        try:
            if self.current_state_file.exists():
                backup_name = f"state_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
                backup_path = self.backup_dir / backup_name
                shutil.copy2(self.current_state_file, backup_path)
        except Exception as e:
            self.logger.warning(f"Failed to create timestamped backup: {e}")
    
    def _rotate_backups(self):
        """Rotate backups, keeping only the most recent N."""
        try:
            # Get all backup files
            backups = sorted(
                self.backup_dir.glob("state_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            # Remove old backups
            for backup in backups[self.backup_count:]:
                backup.unlink()
                self.logger.debug(f"Deleted old backup: {backup.name}")
                
        except Exception as e:
            self.logger.warning(f"Failed to rotate backups: {e}")
    
    # ========================================================================
    # LOAD OPERATIONS
    # ========================================================================
    
    def load_latest_state(self) -> Optional[SystemStateSnapshot]:
        """
        Load latest state snapshot.
        
        Tries in order:
        1. Current state file (if corrupted, return None - no fallback)
        2. Backup state file (only if current doesn't exist)
        3. Most recent timestamped backup (only if current doesn't exist)
        
        Returns:
            SystemStateSnapshot if found and valid, None otherwise
        """
        # Try current state - if it exists but is corrupted, return None
        if self.current_state_file.exists():
            snapshot = self._load_state_file(self.current_state_file)
            if snapshot:
                self.logger.info("Loaded current state", extra={
                    "timestamp": snapshot.timestamp.isoformat(),
                    "positions": len(snapshot.positions),
                    "orders": len(snapshot.pending_orders)
                })
                return snapshot
            else:
                # Current state exists but is corrupted - return None, no fallback
                self.logger.warning("Current state exists but checksum invalid, returning None")
                return None
        
        # Current state doesn't exist - try backup state
        snapshot = self._load_state_file(self.backup_state_file)
        if snapshot:
            self.logger.warning("Loaded from backup state (current state missing)")
            return snapshot
        
        # Try timestamped backups (most recent first)
        backups = sorted(
            self.backup_dir.glob("state_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for backup_file in backups:
            snapshot = self._load_state_file(backup_file)
            if snapshot:
                self.logger.warning(
                    f"Loaded from timestamped backup: {backup_file.name}",
                    extra={"backup": backup_file.name}
                )
                return snapshot
        
        self.logger.warning("No valid state found")
        return None
    
    def _load_state_file(self, file_path: Path) -> Optional[SystemStateSnapshot]:
        """Load and validate state from file."""
        try:
            if not file_path.exists():
                return None
            
            # Read file
            json_str = file_path.read_text(encoding='utf-8')
            data = json.loads(json_str)
            
            # Validate and create snapshot
            snapshot = SystemStateSnapshot.from_dict(data)
            
            return snapshot
            
        except ValueError as e:
            self.logger.error(
                f"State validation failed: {file_path.name}",
                extra={"error": str(e)}
            )
            return None
            
        except Exception as e:
            self.logger.error(
                f"Failed to load state: {file_path.name}",
                extra={"error": str(e)},
                exc_info=True
            )
            return None
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def get_state_age(self) -> Optional[float]:
        """Get age of current state in seconds."""
        if not self.current_state_file.exists():
            return None
        
        mtime = self.current_state_file.stat().st_mtime
        return datetime.now().timestamp() - mtime
    
    def has_saved_state(self) -> bool:
        """Check if saved state exists."""
        return self.current_state_file.exists()
    
    def clear_state(self):
        """Clear all saved state (use with caution)."""
        with self._write_lock:
            try:
                if self.current_state_file.exists():
                    self.current_state_file.unlink()
                
                if self.backup_state_file.exists():
                    self.backup_state_file.unlink()
                
                self.logger.info("Cleared saved state")
                
            except Exception as e:
                self.logger.error(
                    "Failed to clear state",
                    extra={"error": str(e)},
                    exc_info=True
                )
