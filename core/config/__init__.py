"""
Configuration system with Pydantic validation.

Single source of truth for all configuration parameters.
"""

from .schema import (
    ConfigSchema,
    RiskConfig,
    BrokerConfig,
    DataConfig,
    StrategyConfig,
    SessionConfig,
    LoggingConfig,
    BrokerType,
    DataProviderType,
    Timeframe,
    LogLevel,
)

from .loader import (
    ConfigLoader,
    load_config,
)

__all__ = [
    "ConfigSchema",
    "RiskConfig",
    "BrokerConfig",
    "DataConfig",
    "StrategyConfig",
    "SessionConfig",
    "LoggingConfig",
    "BrokerType",
    "DataProviderType",
    "Timeframe",
    "LogLevel",
    "ConfigLoader",
    "load_config",
]
