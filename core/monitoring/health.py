"""
Real-time health monitoring system.

ARCHITECTURE:
- Component-based health checks
- Configurable check intervals
- State tracking (Healthy, Degraded, Unhealthy)
- Alert thresholds with hysteresis
- Automatic recovery attempts
- Thread-safe operation

COMPONENTS MONITORED:
- Broker API connectivity
- Data feed latency
- Disk space
- Memory usage
- Order machine state
- Position reconciliation lag
- Event bus health

Based on LEAN's SystemHealthMonitor and Freqtrade's HealthCheck patterns.
"""

from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from decimal import Decimal
from pathlib import Path
from threading import Thread, Event, Lock
from collections import defaultdict
import time
import psutil

from core.logging import get_logger, LogStream


# ============================================================================
# HEALTH STATUS DEFINITIONS
# ============================================================================

class HealthStatus(Enum):
    """Component health status."""
    HEALTHY = "HEALTHY"        # Operating normally
    DEGRADED = "DEGRADED"      # Operating with issues
    UNHEALTHY = "UNHEALTHY"    # Critical failure
    UNKNOWN = "UNKNOWN"        # Not yet checked


class ComponentType(Enum):
    """System components to monitor."""
    BROKER = "BROKER"
    DATA_FEED = "DATA_FEED"
    DISK_SPACE = "DISK_SPACE"
    MEMORY = "MEMORY"
    ORDER_MACHINE = "ORDER_MACHINE"
    RECONCILIATION = "RECONCILIATION"
    EVENT_BUS = "EVENT_BUS"
    STRATEGY_ENGINE = "STRATEGY_ENGINE"


# ============================================================================
# HEALTH CHECK RESULT
# ============================================================================

@dataclass
class HealthCheckResult:
    """Result of a health check."""
    component: ComponentType
    status: HealthStatus
    message: str
    timestamp: datetime
    metrics: Dict = field(default_factory=dict)
    details: Optional[str] = None
    
    def is_healthy(self) -> bool:
        """Check if component is healthy."""
        return self.status == HealthStatus.HEALTHY
    
    def is_critical(self) -> bool:
        """Check if component requires immediate attention."""
        return self.status == HealthStatus.UNHEALTHY
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "component": self.component.value,
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metrics": {k: str(v) if isinstance(v, Decimal) else v 
                       for k, v in self.metrics.items()},
            "details": self.details
        }


# ============================================================================
# COMPONENT HEALTH CHECK DEFINITION
# ============================================================================

@dataclass
class ComponentCheck:
    """Health check definition for a component."""
    component: ComponentType
    check_function: Callable[[], HealthCheckResult]
    interval_seconds: int = 60  # How often to check
    enabled: bool = True
    last_check: Optional[datetime] = None
    last_result: Optional[HealthCheckResult] = None
    consecutive_failures: int = 0
    failure_threshold: int = 3  # Failures before alert
    
    def should_check_now(self) -> bool:
        """Determine if check should run now."""
        if not self.enabled:
            return False
        
        if self.last_check is None:
            return True
        
        elapsed = (datetime.now(timezone.utc) - self.last_check).total_seconds()
        return elapsed >= self.interval_seconds
    
    def record_check(self, result: HealthCheckResult):
        """Record check result and update failure counter."""
        self.last_check = result.timestamp
        self.last_result = result
        
        if result.status == HealthStatus.UNHEALTHY:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
    
    def requires_alert(self) -> bool:
        """Check if consecutive failures exceed threshold."""
        return self.consecutive_failures >= self.failure_threshold


# ============================================================================
# HEALTH CHECKER
# ============================================================================

class HealthChecker:
    """
    Real-time health monitoring system.
    
    RESPONSIBILITIES:
    - Periodic health checks on all components
    - Status tracking and trending
    - Alert generation on failures
    - Automatic recovery attempts
    - Metrics collection
    
    USAGE:
        checker = HealthChecker(
            broker=broker_connector,
            data_feed=data_feed,
            order_machine=order_machine
        )
        
        checker.register_check(
            ComponentType.BROKER,
            check_function=lambda: check_broker_health(broker),
            interval_seconds=30
        )
        
        checker.start()
        
        # Get current health
        health = checker.get_system_health()
        if not health.is_healthy():
            # Take action
    """
    
    def __init__(
        self,
        check_interval: int = 30,  # Global check interval
        alert_callback: Optional[Callable] = None
    ):
        """
        Initialize health checker.
        
        Args:
            check_interval: Default interval between checks (seconds)
            alert_callback: Function to call on health alerts
        """
        self.check_interval = check_interval
        self.alert_callback = alert_callback
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Component checks registry
        self._checks: Dict[ComponentType, ComponentCheck] = {}
        self._checks_lock = Lock()
        
        # Health history
        self._health_history: Dict[ComponentType, List[HealthCheckResult]] = defaultdict(list)
        self._max_history = 1000  # Keep last 1000 checks per component
        
        # Worker thread
        self._running = False
        self._check_thread: Optional[Thread] = None
        self._stop_event = Event()
        
        # System-level metrics
        self._last_system_check = None
        self._system_start_time = datetime.now(timezone.utc)
        
        self.logger.info("HealthChecker initialized", extra={
            "check_interval": check_interval
        })
    
    # ========================================================================
    # CHECK REGISTRATION
    # ========================================================================
    
    def register_check(
        self,
        component: ComponentType,
        check_function: Callable[[], HealthCheckResult],
        interval_seconds: Optional[int] = None,
        failure_threshold: int = 3
    ):
        """
        Register a health check for a component.
        
        Args:
            component: Component to monitor
            check_function: Function that returns HealthCheckResult
            interval_seconds: Override default interval
            failure_threshold: Failures before alerting
        """
        interval = interval_seconds or self.check_interval
        
        check = ComponentCheck(
            component=component,
            check_function=check_function,
            interval_seconds=interval,
            failure_threshold=failure_threshold
        )
        
        with self._checks_lock:
            self._checks[component] = check
        
        self.logger.info(f"Registered health check: {component.value}", extra={
            "component": component.value,
            "interval": interval,
            "threshold": failure_threshold
        })
    
    def unregister_check(self, component: ComponentType):
        """Remove a health check."""
        with self._checks_lock:
            if component in self._checks:
                del self._checks[component]
                self.logger.info(f"Unregistered health check: {component.value}")
    
    def enable_check(self, component: ComponentType):
        """Enable a health check."""
        with self._checks_lock:
            if component in self._checks:
                self._checks[component].enabled = True
    
    def disable_check(self, component: ComponentType):
        """Disable a health check."""
        with self._checks_lock:
            if component in self._checks:
                self._checks[component].enabled = False
    
    # ========================================================================
    # MONITORING LIFECYCLE
    # ========================================================================
    
    def start(self):
        """Start health monitoring thread."""
        if self._running:
            self.logger.warning("HealthChecker already running")
            return
        
        self._running = True
        self._stop_event.clear()
        
        self._check_thread = Thread(
            target=self._monitoring_loop,
            name="HealthChecker",
            daemon=True
        )
        self._check_thread.start()
        
        self.logger.info("HealthChecker started")
    
    def stop(self):
        """Stop health monitoring thread."""
        if not self._running:
            return
        
        self._running = False
        self._stop_event.set()
        
        if self._check_thread and self._check_thread.is_alive():
            self._check_thread.join(timeout=5)
        
        self.logger.info("HealthChecker stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop."""
        self.logger.info("Health monitoring loop started")
        
        while self._running:
            try:
                # Run health checks
                self._run_checks()
                
                # Wait for next interval (but check stop event)
                self._stop_event.wait(timeout=1.0)
                
            except Exception as e:
                self.logger.error(
                    "Health check loop error",
                    extra={"error": str(e)},
                    exc_info=True
                )
                time.sleep(5)  # Back off on error
        
        self.logger.info("Health monitoring loop ended")
    
    def _run_checks(self):
        """Run all pending health checks."""
        with self._checks_lock:
            checks_to_run = [
                check for check in self._checks.values()
                if check.should_check_now()
            ]
        
        for check in checks_to_run:
            try:
                # Execute check
                result = check.check_function()
                
                # Record result
                check.record_check(result)
                
                # Store in history
                self._health_history[check.component].append(result)
                
                # Trim history
                if len(self._health_history[check.component]) > self._max_history:
                    self._health_history[check.component] = \
                        self._health_history[check.component][-self._max_history:]
                
                # Log result
                log_level = "error" if result.is_critical() else "warning" if \
                           result.status == HealthStatus.DEGRADED else "debug"
                
                getattr(self.logger, log_level)(
                    f"Health check: {check.component.value}",
                    extra={
                        "component": check.component.value,
                        "status": result.status.value,
                        "message": result.message,
                        "metrics": result.metrics
                    }
                )
                
                # Alert if threshold exceeded
                if check.requires_alert() and self.alert_callback:
                    self.alert_callback(result)
                
            except Exception as e:
                self.logger.error(
                    f"Health check failed: {check.component.value}",
                    extra={
                        "component": check.component.value,
                        "error": str(e)
                    },
                    exc_info=True
                )
    
    # ========================================================================
    # STATUS QUERIES
    # ========================================================================
    
    def get_component_health(self, component: ComponentType) -> Optional[HealthCheckResult]:
        """Get latest health status for a component."""
        with self._checks_lock:
            check = self._checks.get(component)
            return check.last_result if check else None
    
    def get_all_health(self) -> Dict[ComponentType, HealthCheckResult]:
        """Get latest health status for all components."""
        with self._checks_lock:
            return {
                comp: check.last_result
                for comp, check in self._checks.items()
                if check.last_result is not None
            }
    
    def get_system_health(self) -> 'SystemHealth':
        """
        Get overall system health summary.
        
        Returns:
            SystemHealth object with status and details
        """
        all_health = self.get_all_health()
        
        # Determine overall status
        if not all_health:
            status = HealthStatus.UNKNOWN
        elif any(h.status == HealthStatus.UNHEALTHY for h in all_health.values()):
            status = HealthStatus.UNHEALTHY
        elif any(h.status == HealthStatus.DEGRADED for h in all_health.values()):
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY
        
        # Count components by status
        status_counts = defaultdict(int)
        for health in all_health.values():
            status_counts[health.status] += 1
        
        # Calculate uptime
        uptime = datetime.now(timezone.utc) - self._system_start_time
        
        return SystemHealth(
            overall_status=status,
            components=all_health,
            status_counts=dict(status_counts),
            uptime_seconds=int(uptime.total_seconds()),
            last_check=datetime.now(timezone.utc)
        )
    
    def get_health_history(
        self,
        component: ComponentType,
        lookback_minutes: int = 60
    ) -> List[HealthCheckResult]:
        """Get health history for a component."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        
        history = self._health_history.get(component, [])
        return [h for h in history if h.timestamp >= cutoff]
    
    # ========================================================================
    # METRICS & ANALYTICS
    # ========================================================================
    
    def get_failure_rate(
        self,
        component: ComponentType,
        lookback_minutes: int = 60
    ) -> float:
        """Calculate failure rate for a component."""
        history = self.get_health_history(component, lookback_minutes)
        
        if not history:
            return 0.0
        
        failures = sum(1 for h in history if h.status == HealthStatus.UNHEALTHY)
        return failures / len(history)
    
    def get_avg_check_interval(self, component: ComponentType) -> Optional[float]:
        """Calculate average actual check interval."""
        history = self._health_history.get(component, [])
        
        if len(history) < 2:
            return None
        
        intervals = []
        for i in range(1, len(history)):
            delta = (history[i].timestamp - history[i-1].timestamp).total_seconds()
            intervals.append(delta)
        
        return sum(intervals) / len(intervals) if intervals else None


# ============================================================================
# SYSTEM HEALTH SUMMARY
# ============================================================================

@dataclass
class SystemHealth:
    """Overall system health summary."""
    overall_status: HealthStatus
    components: Dict[ComponentType, HealthCheckResult]
    status_counts: Dict[HealthStatus, int]
    uptime_seconds: int
    last_check: datetime
    
    def is_healthy(self) -> bool:
        """Check if system is healthy."""
        return self.overall_status == HealthStatus.HEALTHY
    
    def is_degraded(self) -> bool:
        """Check if system is degraded."""
        return self.overall_status == HealthStatus.DEGRADED
    
    def is_unhealthy(self) -> bool:
        """Check if system is unhealthy."""
        return self.overall_status == HealthStatus.UNHEALTHY
    
    def get_unhealthy_components(self) -> List[ComponentType]:
        """Get list of unhealthy components."""
        return [
            comp for comp, health in self.components.items()
            if health.status == HealthStatus.UNHEALTHY
        ]
    
    def get_degraded_components(self) -> List[ComponentType]:
        """Get list of degraded components."""
        return [
            comp for comp, health in self.components.items()
            if health.status == HealthStatus.DEGRADED
        ]
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "overall_status": self.overall_status.value,
            "components": {
                comp.value: health.to_dict()
                for comp, health in self.components.items()
            },
            "status_counts": {
                status.value: count
                for status, count in self.status_counts.items()
            },
            "uptime_seconds": self.uptime_seconds,
            "uptime_formatted": self._format_uptime(),
            "last_check": self.last_check.isoformat()
        }
    
    def _format_uptime(self) -> str:
        """Format uptime as human-readable string."""
        hours = self.uptime_seconds // 3600
        minutes = (self.uptime_seconds % 3600) // 60
        seconds = self.uptime_seconds % 60
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"


# ============================================================================
# SYSTEM RESOURCE CHECKER (BUILT-IN)
# ============================================================================

class SystemResourceChecker:
    """Built-in system resource health checks."""
    
    @staticmethod
    def check_disk_space(
        path: Path,
        warning_threshold_pct: float = 80.0,
        critical_threshold_pct: float = 90.0
    ) -> HealthCheckResult:
        """
        Check disk space.
        
        Args:
            path: Path to check (e.g., Path("/"))
            warning_threshold_pct: Warn if usage above this %
            critical_threshold_pct: Critical if usage above this %
        """
        try:
            usage = psutil.disk_usage(str(path))
            used_pct = usage.percent
            
            if used_pct >= critical_threshold_pct:
                status = HealthStatus.UNHEALTHY
                message = f"Disk critically full: {used_pct:.1f}%"
            elif used_pct >= warning_threshold_pct:
                status = HealthStatus.DEGRADED
                message = f"Disk space low: {used_pct:.1f}%"
            else:
                status = HealthStatus.HEALTHY
                message = f"Disk space OK: {used_pct:.1f}%"
            
            return HealthCheckResult(
                component=ComponentType.DISK_SPACE,
                status=status,
                message=message,
                timestamp=datetime.now(timezone.utc),
                metrics={
                    "used_pct": used_pct,
                    "used_gb": usage.used / (1024**3),
                    "free_gb": usage.free / (1024**3),
                    "total_gb": usage.total / (1024**3)
                }
            )
        except Exception as e:
            return HealthCheckResult(
                component=ComponentType.DISK_SPACE,
                status=HealthStatus.UNKNOWN,
                message=f"Failed to check disk space: {e}",
                timestamp=datetime.now(timezone.utc)
            )
    
    @staticmethod
    def check_memory_usage(
        warning_threshold_pct: float = 80.0,
        critical_threshold_pct: float = 90.0
    ) -> HealthCheckResult:
        """Check memory usage."""
        try:
            mem = psutil.virtual_memory()
            used_pct = mem.percent
            
            if used_pct >= critical_threshold_pct:
                status = HealthStatus.UNHEALTHY
                message = f"Memory critically high: {used_pct:.1f}%"
            elif used_pct >= warning_threshold_pct:
                status = HealthStatus.DEGRADED
                message = f"Memory usage high: {used_pct:.1f}%"
            else:
                status = HealthStatus.HEALTHY
                message = f"Memory usage OK: {used_pct:.1f}%"
            
            return HealthCheckResult(
                component=ComponentType.MEMORY,
                status=status,
                message=message,
                timestamp=datetime.now(timezone.utc),
                metrics={
                    "used_pct": used_pct,
                    "used_gb": mem.used / (1024**3),
                    "available_gb": mem.available / (1024**3),
                    "total_gb": mem.total / (1024**3)
                }
            )
        except Exception as e:
            return HealthCheckResult(
                component=ComponentType.MEMORY,
                status=HealthStatus.UNKNOWN,
                message=f"Failed to check memory: {e}",
                timestamp=datetime.now(timezone.utc)
            )
