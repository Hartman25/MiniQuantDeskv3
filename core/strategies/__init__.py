"""
Multi-Strategy Management System (+3 Safety Points)

COMPONENTS:
- Strategy Coordinator: Conflict resolution, order netting
- Performance Tracker: Auto-cutoff underperformers
- Strategy Manager: Configuration and resource management

USAGE:
    from core.strategies import (
        StrategyCoordinator,
        StrategyPerformanceTracker,
        StrategyManager,
        StrategyConfig
    )
    
    # Initialize systems
    coordinator = StrategyCoordinator()
    tracker = StrategyPerformanceTracker()
    manager = StrategyManager()
    
    # Register strategies
    config = StrategyConfig(
        strategy_id="momentum",
        strategy_type="momentum",
        version="1.0.0",
        parameters={"lookback": 20}
    )
    
    manager.register_strategy(config)
    coordinator.register_strategy("momentum", priority=10)
    
    # Check conflicts before executing
    conflicts = coordinator.check_conflicts(orders)
    
    # Track performance
    tracker.record_trade("momentum", ...)
    
    if not tracker.is_strategy_active("momentum"):
        manager.disable_strategy("momentum")
"""

from core.strategies.coordinator import (
    StrategyCoordinator,
    OrderIntent,
    ConflictType,
    ConflictResult
)

from core.strategies.performance_tracker import (
    StrategyPerformanceTracker,
    StrategyMetrics,
    StrategyStatus,
    TradeRecord
)

from core.strategies.manager import (
    StrategyManager,
    StrategyConfig,
    ResourceUsage,
    HealthStatus
)


__all__ = [
    # Coordinator
    "StrategyCoordinator",
    "OrderIntent",
    "ConflictType",
    "ConflictResult",
    
    # Performance
    "StrategyPerformanceTracker",
    "StrategyMetrics",
    "StrategyStatus",
    "TradeRecord",
    
    # Manager
    "StrategyManager",
    "StrategyConfig",
    "ResourceUsage",
    "HealthStatus",
]
