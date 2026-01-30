"""
Logging Infrastructure for MiniQuantDesk v2

Provides structured, machine-readable logging for:
- Debugging and troubleshooting
- ML/AI training data collection
- Audit trail and compliance
- Performance analysis

Features:
- JSON structured logging (ML-ready)
- Correlation ID tracking (trace order lifecycle)
- Multiple log streams (system, trading, orders, positions, risk)
- Automatic performance timing
- Log rotation and compression
- Thread-safe operation
"""

from .logger import (
    get_logger,
    setup_logging,
    LogContext,
    log_performance,
    set_correlation_id,
    get_correlation_id,
    LogStream,
)

from .formatters import (
    JSONFormatter,
    ConsoleFormatter,
)

from .metrics import (
    PerformanceLogger,
    log_execution_time,
)

__all__ = [
    "get_logger",
    "setup_logging",
    "LogContext",
    "log_performance",
    "set_correlation_id",
    "get_correlation_id",
    "LogStream",
    "JSONFormatter",
    "ConsoleFormatter",
    "PerformanceLogger",
    "log_execution_time",
]
