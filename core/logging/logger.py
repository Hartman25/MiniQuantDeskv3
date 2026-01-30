"""
Core logging module with structured logging and correlation ID tracking.

Architecture:
- Multiple log streams (system, trading, orders, positions, risk, data, performance)
- JSON formatting for ML/AI consumption
- Human-readable console formatting for development
- Correlation ID propagation for request tracing
- Thread-local context storage
- Automatic rotation and compression
"""

import logging
import logging.handlers
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from contextvars import ContextVar
import uuid

# Context variable for correlation ID (thread-safe)
_correlation_id: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)


# ============================================================================
# LOG STREAM DEFINITIONS
# ============================================================================

class LogStream:
    """Log stream identifiers."""
    SYSTEM = "system"           # Infrastructure, startup, shutdown
    TRADING = "trading"         # Strategy signals, decisions
    ORDERS = "orders"           # Order lifecycle events
    POSITIONS = "positions"     # Position changes
    RISK = "risk"              # Risk decisions, limit checks
    DATA = "data"              # Market data quality, staleness
    PERFORMANCE = "performance" # Timing, profiling, metrics
    STRATEGY = "strategy"       # Strategy lifecycle, signals
    PORTFOLIO = "portfolio"     # Portfolio management
    ANALYTICS = "analytics"     # Performance analytics, attribution


# ============================================================================
# CORRELATION ID MANAGEMENT
# ============================================================================

def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """
    Set correlation ID for current context.
    
    Args:
        correlation_id: Optional correlation ID. If None, generates new UUID.
        
    Returns:
        The correlation ID that was set
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    _correlation_id.set(correlation_id)
    return correlation_id


def get_correlation_id() -> Optional[str]:
    """Get correlation ID for current context."""
    return _correlation_id.get()


class LogContext:
    """
    Context manager for scoped correlation ID.
    
    Usage:
        with LogContext("order_123"):
            logger.info("Processing order")  # Includes correlation_id
    """
    
    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id
        self.previous_id = None
        
    def __enter__(self):
        self.previous_id = get_correlation_id()
        set_correlation_id(self.correlation_id)
        return self.correlation_id
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.previous_id:
            set_correlation_id(self.previous_id)
        else:
            _correlation_id.set(None)


# ============================================================================
# CUSTOM LOG RECORD FACTORY
# ============================================================================

_original_factory = logging.getLogRecordFactory()


def _correlation_id_factory(*args, **kwargs):
    """
    Custom log record factory that injects correlation ID.
    """
    record = _original_factory(*args, **kwargs)
    record.correlation_id = get_correlation_id()
    return record


# Install custom factory
logging.setLogRecordFactory(_correlation_id_factory)


# ============================================================================
# LOGGER SETUP
# ============================================================================

_loggers_initialized = False


def setup_logging(
    log_dir: Path = Path("logs"),
    log_level: str = "INFO",
    console_level: str = "INFO",
    json_logs: bool = True,
    max_bytes: int = 10_000_000,  # 10MB
    backup_count: int = 5
) -> None:
    """
    Initialize logging infrastructure.
    
    Creates log files:    - logs/system/system.log (JSON)
    - logs/trading/trading.log (JSON)
    - logs/orders/orders.log (JSON)
    - logs/positions/positions.log (JSON)
    - logs/risk/risk.log (JSON)
    - logs/data/data.log (JSON)
    - logs/performance/performance.log (JSON)
    
    Args:
        log_dir: Base directory for logs
        log_level: File logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_level: Console logging level
        json_logs: If True, use JSON formatting (ML-ready)
        max_bytes: Max bytes per log file before rotation
        backup_count: Number of backup files to keep
    """
    global _loggers_initialized
    
    if _loggers_initialized:
        return
    
    # Create log directories
    for stream in [LogStream.SYSTEM, LogStream.TRADING, LogStream.ORDERS,
                   LogStream.POSITIONS, LogStream.RISK, LogStream.DATA,
                   LogStream.PERFORMANCE]:
        stream_dir = log_dir / stream
        stream_dir.mkdir(parents=True, exist_ok=True)
    
    # Import formatters
    from .formatters import JSONFormatter, ConsoleFormatter
    
    # Configure root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Capture everything, filter at handler level
    
    # Remove existing handlers
    root.handlers = []
    
    # Console handler (human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, console_level.upper()))
    console_handler.setFormatter(ConsoleFormatter())
    root.addHandler(console_handler)
    
    # File handlers for each stream
    file_level = getattr(logging, log_level.upper())
    
    streams = {
        LogStream.SYSTEM: "miniquantdesk.system",
        LogStream.TRADING: "miniquantdesk.trading",
        LogStream.ORDERS: "miniquantdesk.orders",
        LogStream.POSITIONS: "miniquantdesk.positions",
        LogStream.RISK: "miniquantdesk.risk",
        LogStream.DATA: "miniquantdesk.data",
        LogStream.PERFORMANCE: "miniquantdesk.performance",
    }
    
    for stream, logger_name in streams.items():
        log_file = log_dir / stream / f"{stream}.log"
        
        # Rotating file handler
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        handler.setLevel(file_level)
        
        # Use JSON formatter if requested
        if json_logs:
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(correlation_id)s - %(message)s'
            ))
        
        # Add handler to specific logger
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(file_level)
        logger.propagate = True  # Also send to root logger (console)
    
    _loggers_initialized = True
    
    # Log initialization
    system_logger = logging.getLogger("miniquantdesk.system")
    system_logger.info(
        "Logging system initialized",
        extra={
            "log_dir": str(log_dir),
            "log_level": log_level,
            "json_logs": json_logs
        }
    )


def get_logger(stream: str) -> logging.Logger:
    """
    Get logger for specific stream.
    
    Args:
        stream: One of LogStream constants
        
    Returns:
        Logger instance for the stream
        
    Example:
        logger = get_logger(LogStream.ORDERS)
        logger.info("Order submitted", extra={"order_id": "123", "symbol": "SPY"})
    """
    logger_name = f"miniquantdesk.{stream}"
    return logging.getLogger(logger_name)


# ============================================================================
# PERFORMANCE LOGGING DECORATOR
# ============================================================================

def log_performance(stream: str = LogStream.PERFORMANCE):
    """
    Decorator to log function execution time.
    
    Usage:
        @log_performance(LogStream.ORDERS)
        def submit_order(order):
            ...
    """
    def decorator(func):
        import functools
        import time
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(stream)
            start = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                
                logger.debug(
                    f"{func.__name__} completed",
                    extra={
                        "function": func.__name__,
                        "duration_ms": round(elapsed * 1000, 2),
                        "success": True
                    }
                )
                
                return result
                
            except Exception as e:
                elapsed = time.perf_counter() - start
                
                logger.error(
                    f"{func.__name__} failed",
                    extra={
                        "function": func.__name__,
                        "duration_ms": round(elapsed * 1000, 2),
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__
                    },
                    exc_info=True
                )
                
                raise
        
        return wrapper
    return decorator
