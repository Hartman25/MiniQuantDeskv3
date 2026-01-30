"""
Strategy Manager for dynamic parameter adjustment.

ARCHITECTURE:
- Load strategy configurations
- Adjust parameters in real-time
- Monitor strategy health
- Resource allocation (CPU, API, positions)
- Strategy versioning

DESIGN PRINCIPLE:
Treat strategies as managed resources.

RESPONSIBILITIES:
- Configuration management
- Dynamic parameter tuning
- Resource limits enforcement
- Health monitoring
- Strategy lifecycle

Based on institutional strategy management systems.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any
from enum import Enum
import json

from core.logging import get_logger, LogStream


# ============================================================================
# STRATEGY CONFIGURATION
# ============================================================================

@dataclass
class StrategyConfig:
    """Configuration for a trading strategy."""
    strategy_id: str
    strategy_type: str  # "momentum", "mean_reversion", etc.
    version: str
    
    # Parameters
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    # Resource limits
    max_positions: int = 5
    max_daily_trades: int = 20
    max_api_calls_per_minute: int = 60
    
    # Risk parameters
    max_position_size: Decimal = Decimal("10000")
    max_loss_per_trade: Decimal = Decimal("100")
    
    # Status
    enabled: bool = True
    last_modified: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_type": self.strategy_type,
            "version": self.version,
            "parameters": self.parameters,
            "max_positions": self.max_positions,
            "max_daily_trades": self.max_daily_trades,
            "enabled": self.enabled
        }


@dataclass
class ResourceUsage:
    """Resource usage tracking."""
    positions_used: int = 0
    daily_trades: int = 0
    api_calls_this_minute: int = 0
    last_api_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HealthStatus(Enum):
    """Strategy health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


# ============================================================================
# STRATEGY MANAGER
# ============================================================================

class StrategyManager:
    """
    Manage trading strategy lifecycles and resources.
    
    RESPONSIBILITIES:
    - Load/save strategy configurations
    - Adjust parameters dynamically
    - Enforce resource limits
    - Monitor strategy health
    - Version control
    
    RESOURCE MANAGEMENT:
    - Max positions per strategy
    - Max trades per day
    - API call rate limiting
    - CPU/memory allocation (future)
    
    USAGE:
        manager = StrategyManager()
        
        # Load strategy
        config = StrategyConfig(
            strategy_id="momentum_v1",
            strategy_type="momentum",
            version="1.0.0",
            parameters={
                "lookback_period": 20,
                "threshold": 0.02
            },
            max_positions=5,
            max_daily_trades=20
        )
        
        manager.register_strategy(config)
        
        # Check resource availability
        if manager.can_open_position("momentum_v1"):
            # Open position
            manager.record_position_opened("momentum_v1")
        
        # Update parameters
        manager.update_parameter(
            "momentum_v1",
            "threshold",
            0.03
        )
        
        # Monitor health
        health = manager.get_health_status("momentum_v1")
    """
    
    def __init__(self):
        """Initialize strategy manager."""
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Strategy configurations
        self.configs: Dict[str, StrategyConfig] = {}
        
        # Resource usage
        self.usage: Dict[str, ResourceUsage] = {}
        
        # Health tracking
        self.health: Dict[str, HealthStatus] = {}
        
        # Parameter change history
        self.parameter_history: List[Dict] = []
        
        self.logger.info("StrategyManager initialized")
    
    # ========================================================================
    # STRATEGY REGISTRATION
    # ========================================================================
    
    def register_strategy(self, config: StrategyConfig):
        """Register a new strategy."""
        config.last_modified = datetime.now(timezone.utc)
        
        self.configs[config.strategy_id] = config
        self.usage[config.strategy_id] = ResourceUsage()
        self.health[config.strategy_id] = HealthStatus.HEALTHY
        
        self.logger.info(f"Strategy registered: {config.strategy_id}", extra={
            "type": config.strategy_type,
            "version": config.version
        })
    
    def unregister_strategy(self, strategy_id: str):
        """Unregister a strategy."""
        if strategy_id in self.configs:
            del self.configs[strategy_id]
            del self.usage[strategy_id]
            del self.health[strategy_id]
            
            self.logger.info(f"Strategy unregistered: {strategy_id}")
    
    # ========================================================================
    # PARAMETER MANAGEMENT
    # ========================================================================
    
    def update_parameter(
        self,
        strategy_id: str,
        parameter_name: str,
        new_value: Any,
        reason: str = ""
    ):
        """Update a strategy parameter."""
        if strategy_id not in self.configs:
            raise ValueError(f"Strategy not found: {strategy_id}")
        
        config = self.configs[strategy_id]
        old_value = config.parameters.get(parameter_name)
        
        # Update parameter
        config.parameters[parameter_name] = new_value
        config.last_modified = datetime.now(timezone.utc)
        
        # Log change
        change = {
            "timestamp": datetime.now(timezone.utc),
            "strategy_id": strategy_id,
            "parameter": parameter_name,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason
        }
        
        self.parameter_history.append(change)
        
        self.logger.info(f"Parameter updated: {strategy_id}.{parameter_name}", extra={
            "old_value": str(old_value),
            "new_value": str(new_value),
            "reason": reason
        })
    
    def get_parameter(
        self,
        strategy_id: str,
        parameter_name: str,
        default: Any = None
    ) -> Any:
        """Get a strategy parameter."""
        if strategy_id not in self.configs:
            return default
        
        return self.configs[strategy_id].parameters.get(parameter_name, default)
    
    # ========================================================================
    # RESOURCE MANAGEMENT
    # ========================================================================
    
    def can_open_position(self, strategy_id: str) -> bool:
        """Check if strategy can open a new position."""
        if strategy_id not in self.configs:
            return False
        
        config = self.configs[strategy_id]
        usage = self.usage[strategy_id]
        
        # Check position limit
        if usage.positions_used >= config.max_positions:
            return False
        
        # Check daily trade limit
        if usage.daily_trades >= config.max_daily_trades:
            return False
        
        # Check if enabled
        if not config.enabled:
            return False
        
        return True
    
    def record_position_opened(self, strategy_id: str):
        """Record that a position was opened."""
        if strategy_id in self.usage:
            self.usage[strategy_id].positions_used += 1
    
    def record_position_closed(self, strategy_id: str):
        """Record that a position was closed."""
        if strategy_id in self.usage:
            self.usage[strategy_id].positions_used = max(0, self.usage[strategy_id].positions_used - 1)
    
    def record_trade(self, strategy_id: str):
        """Record a trade execution."""
        if strategy_id in self.usage:
            self.usage[strategy_id].daily_trades += 1
    
    def record_api_call(self, strategy_id: str) -> bool:
        """
        Record an API call. Returns False if rate limit exceeded.
        
        Returns:
            True if allowed, False if rate limit exceeded
        """
        if strategy_id not in self.usage:
            return False
        
        usage = self.usage[strategy_id]
        config = self.configs[strategy_id]
        now = datetime.now(timezone.utc)
        
        # Reset counter if minute elapsed
        if (now - usage.last_api_reset).total_seconds() >= 60:
            usage.api_calls_this_minute = 0
            usage.last_api_reset = now
        
        # Check limit
        if usage.api_calls_this_minute >= config.max_api_calls_per_minute:
            return False
        
        usage.api_calls_this_minute += 1
        return True
    
    def reset_daily_counters(self):
        """Reset daily counters (call at start of day)."""
        for usage in self.usage.values():
            usage.daily_trades = 0
        
        self.logger.info("Daily resource counters reset")
    
    # ========================================================================
    # HEALTH MONITORING
    # ========================================================================
    
    def update_health(
        self,
        strategy_id: str,
        status: HealthStatus,
        reason: str = ""
    ):
        """Update strategy health status."""
        if strategy_id in self.health:
            old_status = self.health[strategy_id]
            self.health[strategy_id] = status
            
            if old_status != status:
                self.logger.warning(f"Health status changed: {strategy_id}", extra={
                    "old_status": old_status.value,
                    "new_status": status.value,
                    "reason": reason
                })
    
    def get_health_status(self, strategy_id: str) -> Optional[HealthStatus]:
        """Get strategy health status."""
        return self.health.get(strategy_id)
    
    # ========================================================================
    # ENABLE/DISABLE
    # ========================================================================
    
    def enable_strategy(self, strategy_id: str):
        """Enable a strategy."""
        if strategy_id in self.configs:
            self.configs[strategy_id].enabled = True
            self.logger.info(f"Strategy enabled: {strategy_id}")
    
    def disable_strategy(self, strategy_id: str, reason: str = ""):
        """Disable a strategy."""
        if strategy_id in self.configs:
            self.configs[strategy_id].enabled = False
            self.logger.warning(f"Strategy disabled: {strategy_id}", extra={"reason": reason})
    
    def is_enabled(self, strategy_id: str) -> bool:
        """Check if strategy is enabled."""
        if strategy_id not in self.configs:
            return False
        return self.configs[strategy_id].enabled
    
    # ========================================================================
    # CONFIGURATION I/O
    # ========================================================================
    
    def save_config(self, strategy_id: str, filepath: str):
        """Save strategy configuration to file."""
        if strategy_id not in self.configs:
            raise ValueError(f"Strategy not found: {strategy_id}")
        
        config = self.configs[strategy_id]
        
        with open(filepath, 'w') as f:
            json.dump(config.to_dict(), f, indent=2, default=str)
        
        self.logger.info(f"Config saved: {strategy_id} -> {filepath}")
    
    def load_config(self, filepath: str) -> StrategyConfig:
        """Load strategy configuration from file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        config = StrategyConfig(
            strategy_id=data["strategy_id"],
            strategy_type=data["strategy_type"],
            version=data["version"],
            parameters=data.get("parameters", {}),
            max_positions=data.get("max_positions", 5),
            max_daily_trades=data.get("max_daily_trades", 20),
            enabled=data.get("enabled", True)
        )
        
        self.logger.info(f"Config loaded: {config.strategy_id} <- {filepath}")
        
        return config
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_statistics(self) -> Dict:
        """Get manager statistics."""
        return {
            "total_strategies": len(self.configs),
            "enabled_strategies": sum(1 for c in self.configs.values() if c.enabled),
            "disabled_strategies": sum(1 for c in self.configs.values() if not c.enabled),
            "healthy_strategies": sum(1 for h in self.health.values() if h == HealthStatus.HEALTHY),
            "parameter_changes": len(self.parameter_history),
            "strategies": {
                sid: {
                    "type": config.strategy_type,
                    "version": config.version,
                    "enabled": config.enabled,
                    "health": self.health[sid].value,
                    "positions_used": self.usage[sid].positions_used,
                    "daily_trades": self.usage[sid].daily_trades
                }
                for sid, config in self.configs.items()
            }
        }
    
    def get_all_configs(self) -> List[StrategyConfig]:
        """Get all strategy configurations."""
        return list(self.configs.values())
