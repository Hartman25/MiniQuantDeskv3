"""
Log formatters for structured and human-readable output.

Provides:
- JSONFormatter: Machine-readable JSON logs for ML/AI training
- ConsoleFormatter: Human-readable colored console output
"""

import logging
import json
from datetime import datetime
from typing import Any, Dict
import traceback


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for machine-readable structured logs.
    
    Output format:
    {
        "timestamp": "2025-01-19T10:30:45.123456Z",
        "level": "INFO",
        "logger": "miniquantdesk.orders",
        "correlation_id": "uuid-here",
        "message": "Order submitted",
        "extra": {...}
    }
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, 'correlation_id', None),
            "message": record.getMessage(),
        }
        
        # Add extra fields
        # Exclude standard LogRecord attributes
        standard_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'message', 'pathname', 'process', 'processName', 'relativeCreated',
            'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info',
            'correlation_id'
        }
        
        extra = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith('_'):
                extra[key] = value
        
        if extra:
            log_data["extra"] = extra
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info)
            }
        
        # Add source location for errors
        if record.levelno >= logging.WARNING:
            log_data["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName
            }
        
        return json.dumps(log_data, default=str)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable console formatter with color coding.
    
    Format:
    [2025-01-19 10:30:45] [INFO] [ORDERS] [corr:uuid] Order submitted
    """
    
    # Color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record for console."""
        # Timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        
        # Level with color
        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, self.COLORS['RESET'])
            level_colored = f"{color}{level:8}{self.COLORS['RESET']}"
        else:
            level_colored = f"{level:8}"
        
        # Logger name (extract stream name)
        logger_name = record.name.split('.')[-1].upper() if '.' in record.name else record.name.upper()
        
        # Correlation ID
        corr_id = getattr(record, 'correlation_id', None)
        corr_str = f" [corr:{corr_id[:8]}]" if corr_id else ""
        
        # Message
        message = record.getMessage()
        
        # Build formatted message
        formatted = f"[{timestamp}] [{level_colored}] [{logger_name:12}]{corr_str} {message}"
        
        # Add exception if present
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        
        return formatted
