"""
Append-only transaction log for order events.

CRITICAL PROPERTIES:
1. Append-only (NEVER delete or modify)
2. Newline-delimited JSON (one event per line)
3. Line-buffered (immediate flush to disk)
4. Thread-safe via lock
5. Supports crash recovery replay
6. No in-memory cache (always read from disk)

Based on write-ahead log (WAL) pattern.
"""

import json
import threading
from pathlib import Path
from typing import List, Iterator, Optional
from datetime import datetime
import logging

from core.logging import get_logger, LogStream
from core.time import Clock


# ============================================================================
# TRANSACTION LOG
# ============================================================================

class TransactionLog:
    """
    Append-only log for order lifecycle events.
    
    FILE FORMAT:
    - Newline-delimited JSON (NDJSON)
    - One event per line
    - UTF-8 encoding
    - Line buffering (flush on every write)
    
    GUARANTEES:
    - Events written in order
    - Atomic line writes
    - Immediate disk flush
    - Thread-safe
    - Survives process crash
    
    USAGE:
        log = TransactionLog(Path("data/transactions.log"))
        
        # Append event
        log.append(order_event)
        
        # Read all events (for recovery)
        events = log.read_all()
        
        # Read events since timestamp
        events = log.read_since(timestamp)
    """
    
    def __init__(self, log_path: Path, clock: Clock):
        """
        Initialize transaction log.
        
        Args:
            log_path: Path to log file
            clock: Clock for timestamps (supports backtesting)
        """
        self.log_path = log_path
        self.clock = clock  # NEW: Injectable clock for backtesting
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Ensure parent directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Open file in append mode with line buffering
        # 'a' = append, 'b' = binary is NOT used (we want text mode for line buffering)
        self._file = None
        self._open_file()
        
        self.logger.info("TransactionLog initialized", extra={
            "log_path": str(self.log_path),
            "exists": self.log_path.exists(),
            "size_bytes": self.log_path.stat().st_size if self.log_path.exists() else 0
        })
    
    def _open_file(self):
        """Open log file in append mode with line buffering."""
        self._file = open(
            self.log_path,
            mode='a',
            encoding='utf-8',
            buffering=1  # Line buffered (flush on newline)
        )
    
    def append(self, event) -> None:
        """
        Append event to log.
        
        Thread-safe. Flushes immediately.
        
        Args:
            event: Event object with to_dict() method
            
        Raises:
            TransactionLogError: If write fails
        """
        with self._lock:
            try:
                # Convert event to dict
                event_dict = event.to_dict()
                
                # Add logged_at timestamp (use injected clock for backtest-safe timestamps)
                event_dict['_logged_at'] = self.clock.now().isoformat() + 'Z'
                
                # Serialize to JSON (single line, no newlines in JSON)
                json_line = json.dumps(event_dict, separators=(',', ':'))
                
                # Write line (includes newline)
                self._file.write(json_line + '\n')
                
                # Explicit flush (redundant with line buffering, but safe)
                self._file.flush()
                
            except Exception as e:
                self.logger.error(
                    "Failed to append to transaction log",
                    extra={"error": str(e)},
                    exc_info=True
                )
                raise TransactionLogError(f"Failed to append event: {e}") from e
    
    def read_all(self) -> List[dict]:
        """
        Read all events from log.
        
        Used for crash recovery and auditing.
        
        Returns:
            List of event dictionaries (in order written)
            
        Raises:
            TransactionLogError: If read fails
        """
        with self._lock:
            try:
                if not self.log_path.exists():
                    return []
                
                events = []
                
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue  # Skip blank lines
                        
                        try:
                            event_dict = json.loads(line)
                            events.append(event_dict)
                            
                        except json.JSONDecodeError as e:
                            self.logger.error(
                                f"Invalid JSON at line {line_num}",
                                extra={
                                    "line_num": line_num,
                                    "error": str(e)
                                }
                            )
                            # Continue reading (don't fail on corrupt line)
                
                return events
                
            except Exception as e:
                self.logger.error(
                    "Failed to read transaction log",
                    extra={"error": str(e)},
                    exc_info=True
                )
                raise TransactionLogError(f"Failed to read log: {e}") from e
    
    def read_since(self, since: datetime) -> List[dict]:
        """
        Read events since given timestamp.
        
        Args:
            since: Timestamp to read from (exclusive)
            
        Returns:
            List of events after timestamp
        """
        all_events = self.read_all()
        
        filtered = []
        for event in all_events:
            logged_at_str = event.get('_logged_at')
            if logged_at_str:
                logged_at = datetime.fromisoformat(logged_at_str.replace('Z', '+00:00'))
                if logged_at > since:
                    filtered.append(event)
        
        return filtered
    
    def iter_events(self) -> Iterator[dict]:
        """
        Iterate over events without loading all into memory.
        
        Useful for large logs.
        
        Yields:
            Event dictionaries
        """
        if not self.log_path.exists():
            return
        
        with open(self.log_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    event_dict = json.loads(line)
                    yield event_dict
                    
                except json.JSONDecodeError as e:
                    self.logger.error(
                        f"Invalid JSON at line {line_num}",
                        extra={"line_num": line_num, "error": str(e)}
                    )
                    # Continue iteration
    
    def get_size(self) -> int:
        """
        Get log file size in bytes.
        
        Returns:
            File size in bytes (0 if doesn't exist)
        """
        if not self.log_path.exists():
            return 0
        return self.log_path.stat().st_size
    
    def get_line_count(self) -> int:
        """
        Get number of events in log.
        
        Returns:
            Line count
        """
        if not self.log_path.exists():
            return 0
        
        with open(self.log_path, 'r', encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())
    
    def close(self):
        """Close log file. Call on shutdown."""
        with self._lock:
            if self._file:
                self._file.flush()
                self._file.close()
                self._file = None
                
                self.logger.info("TransactionLog closed", extra={
                    "log_path": str(self.log_path)
                })
    
    def __enter__(self):
        """Context manager support."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support."""
        self.close()
    
    def __del__(self):
        """Destructor: ensure file is closed."""
        if hasattr(self, '_file') and self._file:
            try:
                self._file.close()
            except:
                pass


# ============================================================================
# EXCEPTIONS
# ============================================================================

class TransactionLogError(Exception):
    """Raised on transaction log errors."""
    pass
