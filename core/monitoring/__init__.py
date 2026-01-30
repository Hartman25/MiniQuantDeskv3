"""
Monitoring system package.

PRODUCTION-GRADE MONITORING (+10 Safety Points)

COMPONENTS:
- HealthChecker: System-level health monitoring
- ExecutionMonitor: Order execution quality tracking
- DriftDetector: Real-time position reconciliation

DESIGN STANDARDS:
- LEAN-inspired architecture
- Freqtrade-quality metrics
- Hummingbot-simplicity

USAGE:
    from core.monitoring import (
        HealthChecker,
        HealthStatus,
        ComponentType,
        ExecutionMonitor,
        DriftDetector,
        DriftSeverity
    )
    
    # Initialize components
    health = HealthChecker()
    execution = ExecutionMonitor()
    drift = DriftDetector(position_store, broker)
    
    # Register health checks
    health.register_check(
        ComponentType.BROKER,
        check_function=lambda: check_broker_health(broker),
        interval_seconds=30
    )
    
    # Start monitoring
    health.start()
    
    # Check system health
    system_health = health.get_system_health()
    if not system_health.is_healthy():
        alert_manager.send_critical("System unhealthy!")
"""

# ============================================================================
# HEALTH MONITORING
# ============================================================================

from core.monitoring.health import (
    HealthChecker,
    HealthStatus,
    HealthCheckResult,
    ComponentType,
    ComponentCheck,
    SystemHealth,
    SystemResourceChecker
)

# ============================================================================
# EXECUTION MONITORING
# ============================================================================

from core.monitoring.execution import (
    ExecutionMonitor,
    ExecutionMetric,
    ExecutionSummary,
    OrderStatus
)

# ============================================================================
# DRIFT DETECTION
# ============================================================================

from core.monitoring.drift import (
    DriftDetector,
    DriftType,
    DriftSeverity,
    PositionDrift,
    PositionState
)


__all__ = [
    # Health monitoring
    "HealthChecker",
    "HealthStatus",
    "HealthCheckResult",
    "ComponentType",
    "ComponentCheck",
    "SystemHealth",
    "SystemResourceChecker",
    
    # Execution monitoring
    "ExecutionMonitor",
    "ExecutionMetric",
    "ExecutionSummary",
    "OrderStatus",
    
    # Drift detection
    "DriftDetector",
    "DriftType",
    "DriftSeverity",
    "PositionDrift",
    "PositionState",
]
