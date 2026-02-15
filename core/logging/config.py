"""
Centralized logging configuration for MiniQuantDesk v2.

Creates separate loggers for:
- System events (startup, config, errors)
- Trading events (orders, fills, positions)
- Data events (market data quality, staleness)
- Audit events (state transitions, compliance)
- Performance events (timing, latency)

All logs are:
- Thread-safe
- Rotating (max 10MB per file, 30 days retention)
- Dual format: JSON for machines, readable for humans
- Contextualized with order_id/symbol when available
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional
from datetime import datetime, UTC
import json
from contextvars import ContextVar

# Context variables for request-scoped data
current_order_id: ContextVar[Optional[str]] = ContextVar('current_order_id', default=None)
current_symbol: ContextVar[Optional[str]] = ContextVar('current_symbol', default=None)
current_strategy: ContextVar[Optional[str]] = ContextVar('current_strategy', default=None)


class ContextFilter(logging.Filter):
    """Inject context variables into log records."""
    
    def filter(self, record):
        record.order_id = current_order_id.get()
        record.symbol = current_symbol.get()
        record.strategy = current_strategy.get()
        return True

class JSONFormatter(logging.Formatter):
    """Format log records as JSON for machine parsing."""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.now(UTC).isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add context if available
        if hasattr(record, 'order_id') and record.order_id:
            log_data['order_id'] = record.order_id
        if hasattr(record, 'symbol') and record.symbol:
            log_data['symbol'] = record.symbol
        if hasattr(record, 'strategy') and record.strategy:
            log_data['strategy'] = record.strategy
            
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)

class HumanReadableFormatter(logging.Formatter):
    """Format log records for human readability."""
    
    def format(self, record):
        # Base format
        timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        level = f"{record.levelname:8s}"
        logger = f"{record.name:20s}"
        
        # Build context string
        context_parts = []
        if hasattr(record, 'order_id') and record.order_id:
            context_parts.append(f"order={record.order_id[:8]}")
        if hasattr(record, 'symbol') and record.symbol:
            context_parts.append(f"sym={record.symbol}")
        if hasattr(record, 'strategy') and record.strategy:
            context_parts.append(f"strat={record.strategy}")
            
        context = f"[{' '.join(context_parts)}]" if context_parts else ""
        
        # Format message
        msg = f"{timestamp} {level} {logger} {context:30s} {record.getMessage()}"
        
        # Add exception if present
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
            
        return msg

def setup_logging(
    log_dir: Path,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 30
) -> None:
    """
    Configure logging for the entire application.
    
    Creates separate log files for:
    - system.log (startup, config, errors)
    - trading.log (orders, fills, positions)
    - data.log (market data events)
    - audit.log (state transitions)
    - performance.log (timing metrics)
    
    Args:
        log_dir: Directory for log files
        console_level: Console output level
        file_level: File output level
        max_bytes: Max file size before rotation
        backup_count: Number of backup files to keep
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories
    (log_dir / "system").mkdir(exist_ok=True)
    (log_dir / "trading").mkdir(exist_ok=True)
    (log_dir / "data").mkdir(exist_ok=True)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Context filter (add to all handlers)
    context_filter = ContextFilter()
    
    # Console handler (human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(HumanReadableFormatter())
    console_handler.addFilter(context_filter)
    root_logger.addHandler(console_handler)
    
    # System logger
    system_logger = logging.getLogger('system')
    system_handler = logging.handlers.RotatingFileHandler(
        log_dir / "system" / "system.log",
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    system_handler.setLevel(file_level)
    system_handler.setFormatter(JSONFormatter())
    system_handler.addFilter(context_filter)
    system_logger.addHandler(system_handler)
    
    # Trading logger
    trading_logger = logging.getLogger('trading')
    trading_handler = logging.handlers.RotatingFileHandler(
        log_dir / "trading" / "trading.log",
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    trading_handler.setLevel(file_level)
    trading_handler.setFormatter(JSONFormatter())
    trading_handler.addFilter(context_filter)
    trading_logger.addHandler(trading_handler)
    
    # Data logger
    data_logger = logging.getLogger('data')
    data_handler = logging.handlers.RotatingFileHandler(
        log_dir / "data" / "data.log",
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    data_handler.setLevel(file_level)
    data_handler.setFormatter(JSONFormatter())
    data_handler.addFilter(context_filter)
    data_logger.addHandler(data_handler)
    
    # Audit logger (state transitions - NEVER rotate)
    audit_logger = logging.getLogger('audit')
    audit_handler = logging.FileHandler(
        log_dir / "trading" / "audit.log",
        mode='a'
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(JSONFormatter())
    audit_handler.addFilter(context_filter)
    audit_logger.addHandler(audit_handler)
    
    # Performance logger
    perf_logger = logging.getLogger('performance')
    perf_handler = logging.handlers.RotatingFileHandler(
        log_dir / "system" / "performance.log",
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    perf_handler.setLevel(file_level)
    perf_handler.setFormatter(JSONFormatter())
    perf_handler.addFilter(context_filter)
    perf_logger.addHandler(perf_handler)
    
    # Log startup message
    system_logger.info("=" * 60)
    system_logger.info("MiniQuantDesk v2 Logging System Initialized")
    system_logger.info(f"Log directory: {log_dir}")
    system_logger.info(f"Console level: {logging.getLevelName(console_level)}")
    system_logger.info(f"File level: {logging.getLevelName(file_level)}")
    system_logger.info("=" * 60)


def get_logger(name: str) -> logging.Logger:
    """Get a logger by name."""
    return logging.getLogger(name)
