"""
Performance metrics logging for profiling and optimization.

Provides:
- PerformanceLogger: Dedicated performance metrics logger
- log_execution_time: Context manager for timing code blocks
- Automatic timing statistics collection
"""

import time
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict


@dataclass
class PerformanceMetrics:
    """Performance metrics for a single operation."""
    operation: str
    duration_ms: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class PerformanceLogger:
    """
    Dedicated logger for performance metrics.
    
    Collects timing statistics and logs to performance stream.
    """
    
    def __init__(self):
        from .logger import get_logger, LogStream
        self.logger = get_logger(LogStream.PERFORMANCE)
        
        # Statistics tracking
        self._stats = defaultdict(list)  # operation -> [durations]
    
    def log_metric(
        self,
        operation: str,
        duration_ms: float,
        success: bool = True,
        **metadata
    ):
        """
        Log a performance metric.
        
        Args:
            operation: Operation name (e.g., "order_submission")
            duration_ms: Duration in milliseconds
            success: Whether operation succeeded
            **metadata: Additional metadata
        """
        # Record for statistics
        if success:
            self._stats[operation].append(duration_ms)
        
        # Log
        self.logger.info(
            f"{operation} performance",
            extra={
                "operation": operation,
                "duration_ms": round(duration_ms, 2),
                "success": success,
                **metadata
            }
        )
    
    def get_stats(self, operation: str) -> Optional[Dict[str, float]]:
        """
        Get statistics for an operation.
        
        Returns:
            Dict with min, max, mean, p50, p95, p99 or None
        """
        durations = self._stats.get(operation)
        if not durations:
            return None
        
        sorted_durations = sorted(durations)
        n = len(sorted_durations)
        
        return {
            "count": n,
            "min_ms": round(min(sorted_durations), 2),
            "max_ms": round(max(sorted_durations), 2),
            "mean_ms": round(sum(sorted_durations) / n, 2),
            "p50_ms": round(sorted_durations[n // 2], 2),
            "p95_ms": round(sorted_durations[int(n * 0.95)], 2),
            "p99_ms": round(sorted_durations[int(n * 0.99)], 2) if n >= 100 else None
        }
    
    def log_stats(self, operation: str):
        """Log statistics summary for an operation."""
        stats = self.get_stats(operation)
        if stats:
            self.logger.info(
                f"{operation} statistics",
                extra={"operation": operation, "stats": stats}
            )


# Global performance logger instance
_perf_logger = None


def get_performance_logger() -> PerformanceLogger:
    """Get or create global performance logger instance."""
    global _perf_logger
    if _perf_logger is None:
        _perf_logger = PerformanceLogger()
    return _perf_logger


@contextmanager
def log_execution_time(
    operation: str,
    logger: Optional[logging.Logger] = None,
    **metadata
):
    """
    Context manager to log execution time.
    
    Usage:
        with log_execution_time("fetch_market_data", symbol="SPY"):
            data = fetch_data("SPY")
    
    Args:
        operation: Operation name
        logger: Optional logger (uses performance logger if None)
        **metadata: Additional metadata to log
    """
    start = time.perf_counter()
    perf_logger = get_performance_logger()
    
    try:
        yield
        
        # Success
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        perf_logger.log_metric(operation, elapsed, success=True, **metadata)
        
        if logger:
            logger.debug(
                f"{operation} completed in {elapsed:.2f}ms",
                extra={"operation": operation, "duration_ms": round(elapsed, 2), **metadata}
            )
            
    except Exception as e:
        # Failure
        elapsed = (time.perf_counter() - start) * 1000
        perf_logger.log_metric(
            operation,
            elapsed,
            success=False,
            error=str(e),
            error_type=type(e).__name__,
            **metadata
        )
        
        if logger:
            logger.error(
                f"{operation} failed after {elapsed:.2f}ms: {e}",
                extra={
                    "operation": operation,
                    "duration_ms": round(elapsed, 2),
                    "error": str(e),
                    "error_type": type(e).__name__,
                    **metadata
                },
                exc_info=True
            )
        
        raise
