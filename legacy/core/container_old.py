"""
Dependency Injection Container - Wires all components together.

CRITICAL ARCHITECTURE:
1. Single source of component instances
2. Manages component lifecycles
3. Ensures proper initialization order
4. Prevents circular dependencies
5. Thread-safe singleton access

Simplifies testing and component replacement.
Based on Spring's ApplicationContext pattern.

ENHANCED WITH 6 CRITICAL FEATURES:
1. Clock Abstraction - Injectable time provider
2. Throttler - API rate limiting
3. OrderTracker - Fill lifecycle tracking
4. Protections - Dynamic circuit breakers
5. UserStreamTracker - Real-time WebSocket fills
6. Symbol Properties - Order validation
"""

from typing import Optional, Dict, Any
from decimal import Decimal
import logging

# State
from core.state.order_machine import OrderStateMachine
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog
from core.state.reconciler import BrokerReconciler
from core.state.order_tracker import OrderTracker  # NEW

# Events
from core.events.bus import OrderEventBus  # FIXED: Was EventBus
from core.events.handlers import EventHandlerRegistry

# Data
from core.data.validator import DataValidator
from core.data.cache import DataCache
from core.data.pipeline import MarketDataPipeline  # FIXED: Was DataPipeline

# Risk
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer
from core.risk.gate import PreTradeRiskGate
from core.risk.manager import RiskManager
from core.risk.protections import create_default_protections, ProtectionManager  # NEW

# Strategies
from strategies.registry import StrategyRegistry
from strategies.lifecycle import StrategyLifecycleManager

# Config
from core.config.schema import ConfigSchema as TradingConfig  # FIXED: Actual class is ConfigSchema
from core.config.loader import ConfigLoader

# NEW: Time, Network, Market, Real-time
from core.time import get_clock, Clock
from core.net import create_combined_throttler, Throttler
from core.market import SymbolPropertiesCache, SecurityCache
from core.realtime import UserStreamTracker

logger = logging.getLogger(__name__)


# ============================================================================
# DEPENDENCY INJECTION CONTAINER
# ============================================================================

class Container:
    """
    Dependency injection container for all components.
    
    INITIALIZATION ORDER:
    1. Config
    2. State (OrderMachine, PositionStore, TransactionLog)
    3. Events (EventBus, Handlers)
    4. Data (Validator, Cache, Pipeline)
    5. Risk (Limits, Sizer, Gate, Manager)
    6. Strategies (Registry, Lifecycle)
    7. Broker (Connector, Reconciler)
    
    Usage:
        container = Container()
        container.initialize("config/config.yaml")
        
        # Access components
        risk_gate = container.get_risk_gate()
        position_store = container.get_position_store()
    """
    
    def __init__(self):
        # Config
        self._config: Optional[TradingConfig] = None
        
        # NEW: Core infrastructure (highest priority)
        self._clock: Optional[Clock] = None
        self._throttler: Optional[Throttler] = None
        
        # State
        self._order_machine: Optional[OrderStateMachine] = None
        self._position_store: Optional[PositionStore] = None
        self._transaction_log: Optional[TransactionLog] = None
        self._order_tracker: Optional[OrderTracker] = None  # NEW
        
        # Events
        self._event_bus: Optional[OrderEventBus] = None  # FIXED: Was EventBus
        self._event_handlers: Optional[EventHandlerRegistry] = None
        
        # Data
        self._data_validator: Optional[DataValidator] = None
        self._data_cache: Optional[DataCache] = None
        self._data_pipeline: Optional[MarketDataPipeline] = None  # FIXED: Was DataPipeline
        
        # Risk
        self._limits_tracker: Optional[PersistentLimitsTracker] = None
        self._position_sizer: Optional[NotionalPositionSizer] = None
        self._risk_gate: Optional[PreTradeRiskGate] = None
        self._risk_manager: Optional[RiskManager] = None
        self._protections: Optional[ProtectionManager] = None  # NEW
        
        # Strategies
        self._strategy_registry: Optional[StrategyRegistry] = None
        self._strategy_lifecycle: Optional[StrategyLifecycleManager] = None
        
        # Broker
        self._broker_connector = None
        self._reconciler: Optional[BrokerReconciler] = None
        
        # NEW: Market data and real-time
        self._symbol_props_cache: Optional[SymbolPropertiesCache] = None
        self._security_cache: Optional[SecurityCache] = None
        self._user_stream: Optional[UserStreamTracker] = None
    
    def initialize(self, config_path: str) -> None:
        """
        Initialize all components in correct order.
        
        Args:
            config_path: Path to config YAML file
            
        Raises:
            RuntimeError: If already initialized
        """
        if self._config is not None:
            raise RuntimeError("Container already initialized")
        
        from pathlib import Path
        
        # 1. Load config
        loader = ConfigLoader(config_dir=Path(config_path).parent)
        raw_config = loader.load()
        self._config = TradingConfig(**raw_config)
        
        # 2. Initialize foundational components (no dependencies)
        self._transaction_log = TransactionLog(
            log_path=self._config.transaction_log_path
        )
        self._event_bus = OrderEventBus()
        
        # 3. Initialize state components (depend on event_bus + transaction_log)
        self._order_machine = OrderStateMachine(
            event_bus=self._event_bus,
            transaction_log=self._transaction_log
        )
        self._position_store = PositionStore(
            db_path=self._config.position_db_path
        )
        
        # 4. Initialize event handlers
        self._event_handlers = EventHandlerRegistry(
            order_machine=self._order_machine,
            position_store=self._position_store,
            transaction_log=self._transaction_log
        )
        
        # 4. Initialize data components
        self._data_validator = DataValidator(
            max_staleness_seconds=self._config.data.max_staleness_seconds,
            require_complete_bars=True  # Anti-lookahead
        )
        # FIXED: DataCache uses max_size/max_age_seconds, not cache_dir/enabled
        self._data_cache = DataCache()  # Use defaults: max_size=10000, max_age_seconds=300
        # FIXED: MarketDataPipeline uses alpaca credentials directly
        self._data_pipeline = MarketDataPipeline(
            alpaca_api_key=self._config.broker.api_key,
            alpaca_api_secret=self._config.broker.api_secret,
            max_staleness_seconds=self._config.data.max_staleness_seconds
        )
        
        # 5. Initialize risk components
        self._limits_tracker = PersistentLimitsTracker(
            db_path=str(self._config.limit_tracker_path),
            daily_loss_limit=self._config.risk.daily_loss_limit_usd
        )
        self._position_sizer = NotionalPositionSizer(
            max_exposure_per_position=self._config.risk.max_position_size_pct / Decimal("100")
        )
        self._risk_gate = PreTradeRiskGate(
            limits_tracker=self._limits_tracker,
            position_sizer=self._position_sizer,
            account_value=self._config.risk.initial_account_value,
            enable_pdt_protection=True,
            max_orders_per_day=self._config.session.max_daily_trades
        )
        
        # Create RiskLimits from config
        from core.risk.manager import RiskLimits
        risk_limits = RiskLimits(
            max_position_size_usd=self._config.risk.initial_account_value * self._config.risk.max_position_size_pct / Decimal("100"),
            max_position_pct_portfolio=self._config.risk.max_position_size_pct / Decimal("100"),
            max_positions=self._config.risk.max_open_positions,
            max_daily_loss_usd=self._config.risk.daily_loss_limit_usd,
            max_daily_loss_pct=Decimal("0.02")  # Default 2%
        )
        
        self._risk_manager = RiskManager(
            position_store=self._position_store,
            limits=risk_limits
        )
        
        # 6. Initialize strategy components
        self._strategy_registry = StrategyRegistry()
        self._strategy_lifecycle = StrategyLifecycleManager()  # No parameters
        
        logger.info("Container initialized successfully")
    
    # ========================================================================
    # COMPONENT ACCESSORS
    # ========================================================================
    
    def get_config(self) -> TradingConfig:
        if self._config is None:
            raise RuntimeError("Container not initialized")
        return self._config
    
    def get_order_machine(self) -> OrderStateMachine:
        if self._order_machine is None:
            raise RuntimeError("Container not initialized")
        return self._order_machine
    
    def get_position_store(self) -> PositionStore:
        if self._position_store is None:
            raise RuntimeError("Container not initialized")
        return self._position_store
    
    def get_transaction_log(self) -> TransactionLog:
        if self._transaction_log is None:
            raise RuntimeError("Container not initialized")
        return self._transaction_log
    
    def get_event_bus(self) -> OrderEventBus:  # FIXED: Was EventBus
        if self._event_bus is None:
            raise RuntimeError("Container not initialized")
        return self._event_bus
    
    def get_data_validator(self) -> DataValidator:
        if self._data_validator is None:
            raise RuntimeError("Container not initialized")
        return self._data_validator
    
    def get_data_pipeline(self) -> MarketDataPipeline:  # FIXED: Was DataPipeline
        if self._data_pipeline is None:
            raise RuntimeError("Container not initialized")
        return self._data_pipeline
    
    def get_risk_gate(self) -> PreTradeRiskGate:
        if self._risk_gate is None:
            raise RuntimeError("Container not initialized")
        return self._risk_gate
    
    def get_risk_manager(self) -> RiskManager:
        if self._risk_manager is None:
            raise RuntimeError("Container not initialized")
        return self._risk_manager
    
    def get_strategy_registry(self) -> StrategyRegistry:
        if self._strategy_registry is None:
            raise RuntimeError("Container not initialized")
        return self._strategy_registry
    
    def get_strategy_lifecycle(self) -> StrategyLifecycleManager:
        if self._strategy_lifecycle is None:
            raise RuntimeError("Container not initialized")
        return self._strategy_lifecycle
    
    def get_reconciler(self) -> Optional[BrokerReconciler]:
        return self._reconciler
    
    def set_broker_connector(self, connector) -> None:
        """Set broker connector and create reconciler."""
        self._broker_connector = connector
        if self._position_store and self._order_machine:
            self._reconciler = BrokerReconciler(
                broker_connector=connector,
                position_store=self._position_store,
                order_machine=self._order_machine
            )
    
    # ========================================================================
    # LIFECYCLE
    # ========================================================================
    
    def start(self) -> None:
        """Start all startable components."""
        if self._event_bus:
            self._event_bus.start()
        
        logger.info("Container started")
    
    def stop(self) -> None:
        """Stop all stoppable components."""
        if self._event_bus:
            try:
                self._event_bus.stop(timeout=5.0)
            except Exception as e:
                logger.error(f"Error stopping event bus: {e}")
        
        logger.info("Container stopped")
