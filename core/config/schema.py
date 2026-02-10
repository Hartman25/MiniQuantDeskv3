"""
Configuration schema using Pydantic for validation.

Single source of truth for all configuration parameters.
Validates on load, fails fast on invalid config.

Based on Freqtrade's config discipline and LEAN's parameter validation.
"""

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from typing import List, Optional, Dict, Any
from decimal import Decimal
from pathlib import Path
from enum import Enum
import os


# ============================================================================
# ENUMS
# ============================================================================

class BrokerType(str, Enum):
    """Supported broker types."""
    ALPACA = "alpaca"
    MOCK = "mock"


class DataProviderType(str, Enum):
    """Supported data providers."""
    ALPACA = "alpaca"
    POLYGON = "polygon"
    YFINANCE = "yfinance"


class Timeframe(str, Enum):
    """Supported timeframes."""
    ONE_MIN = "1Min"
    FIVE_MIN = "5Min"
    FIFTEEN_MIN = "15Min"
    THIRTY_MIN = "30Min"
    ONE_HOUR = "1Hour"
    ONE_DAY = "1Day"


class LogLevel(str, Enum):
    """Log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ============================================================================
# RISK CONFIGURATION
# ============================================================================

class RiskConfig(BaseModel):
    """
    Risk management parameters.
    
    RULES:
    - All limits must be positive
    - Weekly limit >= daily limit
    - Max position size <= 50%
    - Risk per trade <= 5%
    """
    
    # Account setup (TEMPORARY: should come from broker in Phase 3)
    initial_account_value: Decimal = Field(
        ge=Decimal("100.0"),
        default=Decimal("1000.0"),
        description="Initial account equity for paper trading (TEMPORARY)"
    )
    
    # Position limits
    max_open_positions: int = Field(
        ge=1,
        le=10,
        default=1,
        description="Maximum number of simultaneous positions"
    )
    
    max_position_size_pct: Decimal = Field(
        ge=Decimal("1.0"),
        le=Decimal("50.0"),
        default=Decimal("10.0"),
        description="Maximum position size as % of account"
    )
    
    # Loss limits (USD)
    daily_loss_limit_usd: Decimal = Field(
        ge=Decimal("1.0"),
        default=Decimal("100.0"),
        description="Maximum daily loss in USD"
    )
    
    weekly_loss_limit_usd: Decimal = Field(
        ge=Decimal("1.0"),
        default=Decimal("300.0"),
        description="Maximum weekly loss in USD"
    )
    
    # Per-trade risk
    risk_per_trade_pct: Decimal = Field(
        ge=Decimal("0.1"),
        le=Decimal("5.0"),
        default=Decimal("1.0"),
        description="Risk per trade as % of account"
    )
    
    # Circuit breaker
    circuit_breaker_enabled: bool = Field(
        default=True,
        description="Enable circuit breaker on rapid losses"
    )
    
    circuit_breaker_loss_pct: Decimal = Field(
        ge=Decimal("1.0"),
        le=Decimal("10.0"),
        default=Decimal("5.0"),
        description="Circuit breaker trigger loss %"
    )
    
    halt_duration_minutes: int = Field(
        ge=5,
        le=120,
        default=30,
        description="Circuit breaker halt duration"
    )
    
    @model_validator(mode='after')
    def validate_loss_limits(self):
        """Ensure weekly >= daily."""
        if self.weekly_loss_limit_usd < self.daily_loss_limit_usd:
            raise ValueError(
                f"Weekly loss limit ({self.weekly_loss_limit_usd}) must be "
                f">= daily loss limit ({self.daily_loss_limit_usd})"
            )
        return self
    
    # PATCH 4: Migrated from class Config to model_config (Pydantic v2)
    model_config = ConfigDict(validate_assignment=True)


# ============================================================================
# BROKER CONFIGURATION
# ============================================================================

class BrokerConfig(BaseModel):
    """
    Broker connection settings.
    
    Secrets (api_key, api_secret) loaded from environment.
    """
    
    name: BrokerType = Field(
        default=BrokerType.ALPACA,
        description="Broker type"
    )
    
    api_key: str = Field(
        ...,
        min_length=10,
        description="Broker API key"
    )
    
    api_secret: str = Field(
        ...,
        min_length=10,
        description="Broker API secret"
    )
    
    base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Broker API base URL"
    )
    
    paper_trading: bool = Field(
        default=True,
        description="Use paper trading account"
    )
    
    @field_validator('api_key', 'api_secret')
    @classmethod
    def no_placeholder_values(cls, v: str) -> str:
        """Prevent placeholder values (except during pytest)."""
        # Pytest sets this env var when running tests
        if os.getenv("PYTEST_CURRENT_TEST"):
            return v

        if v in ("YOUR_API_KEY", "PLACEHOLDER", ""):
            raise ValueError("API credentials cannot be placeholder values")
        return v

# ============================================================================
# DATA CONFIGURATION
# ============================================================================

class DataConfig(BaseModel):
    """Market data provider settings."""
    
    primary_provider: DataProviderType = Field(
        default=DataProviderType.ALPACA,
        description="Primary data provider"
    )
    
    fallback_providers: List[DataProviderType] = Field(
        default_factory=list,
        description="Fallback providers in priority order"
    )
    
    max_staleness_seconds: int = Field(
        ge=10,
        le=300,
        default=90,
        description="Maximum acceptable data age"
    )
    
    cache_enabled: bool = Field(
        default=True,
        description="Enable data caching"
    )
    
    cache_dir: Path = Field(
        default=Path("data/cache"),
        description="Cache directory path"
    )


# ============================================================================
# STRATEGY CONFIGURATION
# ============================================================================

class StrategyConfig(BaseModel):
    """Individual strategy configuration."""
    
    name: str = Field(
        ...,
        min_length=1,
        description="Strategy class name"
    )
    
    enabled: bool = Field(
        default=True,
        description="Enable this strategy"
    )
    
    symbols: List[str] = Field(
        ...,
        min_length=1,
        description="Symbols to trade"
    )
    
    timeframe: Timeframe = Field(
        default=Timeframe.ONE_MIN,
        description="Bar timeframe"
    )
    
    lookback_bars: int = Field(
        ge=10,
        le=500,
        default=50,
        description="Bars required for analysis"
    )
    
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific parameters"
    )
    
    @field_validator('symbols')
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
        """Ensure symbols are uppercase and valid."""
        validated = []
        for symbol in v:
            symbol = symbol.strip().upper()
            if not symbol:
                raise ValueError("Empty symbol not allowed")
            if len(symbol) > 10:
                raise ValueError(f"Symbol too long: {symbol}")
            validated.append(symbol)
        return validated


# ============================================================================
# SESSION CONFIGURATION
# ============================================================================

class SessionConfig(BaseModel):
    """Session and execution settings."""
    
    cycle_interval_seconds: int = Field(
        ge=30,
        le=300,
        default=60,
        description="Seconds between trade cycles"
    )
    
    max_daily_trades: int = Field(
        ge=1,
        le=10,
        default=3,
        description="Maximum trades per day (PDT protection)"
    )
    
    trading_hours_only: bool = Field(
        default=True,
        description="Trade only during market hours"
    )
    
    startup_recovery_enabled: bool = Field(
        default=True,
        description="Run recovery protocol on startup"
    )

    # -- Adaptive loop cadence (optional overrides) -------------------------
    # 0 means "not set" → fall back to env var → hard default.
    closed_interval_s: int = Field(
        ge=0,
        le=600,
        default=0,
        description="Sleep seconds when market is closed (0 = use env/default)"
    )
    pre_open_interval_s: int = Field(
        ge=0,
        le=300,
        default=0,
        description="Sleep seconds in pre-open window (0 = use env/default)"
    )
    pre_open_window_m: int = Field(
        ge=0,
        le=60,
        default=0,
        description="Minutes before open to switch to pre-open cadence (0 = use env/default)"
    )


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

class LoggingConfig(BaseModel):
    """Logging configuration."""
    
    log_dir: Path = Field(
        default=Path("logs"),
        description="Base log directory"
    )
    
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="File logging level"
    )
    
    console_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Console logging level"
    )
    
    json_logs: bool = Field(
        default=True,
        description="Use JSON formatting (ML-ready)"
    )
    
    max_bytes: int = Field(
        ge=1_000_000,
        le=100_000_000,
        default=10_000_000,
        description="Max bytes per log file"
    )
    
    backup_count: int = Field(
        ge=1,
        le=20,
        default=5,
        description="Number of backup files"
    )


# ============================================================================
# MASTER CONFIGURATION
# ============================================================================

class ConfigSchema(BaseModel):
    """
    Master configuration schema.
    
    Single source of truth for all parameters.
    Validates on load, fails fast on invalid config.
    
    Usage:
        config = ConfigSchema.from_yaml("config/config.yaml")
        config = ConfigSchema.from_env()
    """
    
    # Core configuration blocks
    risk: RiskConfig
    broker: BrokerConfig
    data: DataConfig
    strategies: List[StrategyConfig] = Field(min_length=1)
    session: SessionConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    # Paths
    position_db_path: Path = Field(
        default=Path("data/positions/positions.db")
    )
    
    transaction_log_path: Path = Field(
        default=Path("data/transactions/transactions.log")
    )
    
    limit_tracker_path: Path = Field(
        default=Path("data/limits/limits.json")
    )
    
    # PATCH 4: Migrated from class Config to model_config (Pydantic v2)
    model_config = ConfigDict(validate_assignment=True)
    
    @model_validator(mode='after')
    def ensure_directories_exist(self):
        """Create necessary directories."""
        for path_attr in ['position_db_path', 'transaction_log_path', 'limit_tracker_path']:
            path = getattr(self, path_attr)
            path.parent.mkdir(parents=True, exist_ok=True)
        
        self.data.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logging.log_dir.mkdir(parents=True, exist_ok=True)
        
        return self
    
    @classmethod
    def from_yaml(cls, path: Path) -> "ConfigSchema":
        """
        Load config from YAML file.
        
        Args:
            path: Path to config.yaml
            
        Returns:
            Validated ConfigSchema instance
        """
        import yaml
        
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        
        return cls(**data)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfigSchema":
        """Load config from dictionary."""
        return cls(**data)
    
    def to_yaml(self, path: Path) -> None:
        """Save config to YAML file."""
        import yaml
        
        # Convert to dict, handle Decimals
        data = self.model_dump(mode='json')
        
        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    
    def validate_small_account(self, account_value: Decimal) -> List[str]:
        """
        Validate configuration for small accounts.
        
        Returns list of warnings/errors.
        """
        issues = []
        
        # Check if symbols are tradeable
        for strategy in self.strategies:
            for symbol in strategy.symbols:
                # Estimate minimum position size
                # Assume worst case: symbol price = $600 (like SPY)
                assumed_price = Decimal("600")
                min_position_value = assumed_price  # 1 share
                max_position_pct = self.risk.max_position_size_pct / 100
                max_allowed = account_value * max_position_pct
                
                if min_position_value > max_allowed:
                    issues.append(
                        f"Symbol {symbol} likely untradeable: "
                        f"min ~${min_position_value} exceeds "
                        f"{self.risk.max_position_size_pct}% of ${account_value}"
                    )
        
        return issues
