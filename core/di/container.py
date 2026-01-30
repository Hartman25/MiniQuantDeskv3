"""
Dependency Injection Container - Enhanced with 6 critical features.

NEW FEATURES INTEGRATED:
1. Clock Abstraction - Injectable time provider (eliminates datetime.now() bugs)
2. Throttler - API rate limiting (prevents account bans)
3. OrderTracker - Fill lifecycle tracking (orphan/shadow detection)
4. Protections - Dynamic circuit breakers (loss limits, cooldowns)
5. UserStreamTracker - Real-time WebSocket fills (instant notifications)
6. Symbol Properties - Order validation (price/quantity rounding)
"""

from typing import Optional
from decimal import Decimal
from datetime import time
import logging
from pathlib import Path

# State
from core.state.order_machine import OrderStateMachine
from core.state.position_store import PositionStore
from core.state.transaction_log import TransactionLog
from core.state.reconciler import BrokerReconciler, StartupReconciler
from core.state.order_tracker import OrderTracker, InFlightOrder, FillEvent, OrderSide, OrderType

# Events
from core.events.bus import OrderEventBus
from core.events.handlers import EventHandlerRegistry

# Data
from core.data.validator import DataValidator
from core.data.cache import DataCache
from core.data.pipeline import MarketDataPipeline

# Risk
from core.risk.limits import PersistentLimitsTracker
from core.risk.sizing import NotionalPositionSizer
from core.risk.gate import PreTradeRiskGate
from core.risk.manager import RiskManager, RiskLimits
from core.risk.protections import create_default_protections, ProtectionManager

# Execution (NEW)
from core.execution.engine import OrderExecutionEngine

# Strategies
from strategies.registry import StrategyRegistry
from strategies.lifecycle import StrategyLifecycleManager

# Config
from core.config.schema import ConfigSchema as TradingConfig
from core.config.loader import ConfigLoader

# NEW: Time, Network, Market, Real-time
from core.time import get_clock, Clock
from core.net import create_combined_throttler, Throttler
from core.market import SymbolPropertiesCache, SecurityCache
from core.realtime import UserStreamTracker

logger = logging.getLogger(__name__)


class Container:
    """
    Enhanced Dependency Injection Container.
    
    Manages all system components with proper initialization order.
    Now includes 6 critical institutional-grade features.
    """
    
    def __init__(self):
        # Config
        self._config: Optional[TradingConfig] = None
        
        # NEW: Core infrastructure (initialized first)
        self._clock: Optional[Clock] = None
        self._throttler: Optional[Throttler] = None
        
        # State
        self._order_machine: Optional[OrderStateMachine] = None
        self._position_store: Optional[PositionStore] = None
        self._transaction_log: Optional[TransactionLog] = None
        self._order_tracker: Optional[OrderTracker] = None  # NEW
        
        # Events
        self._event_bus: Optional[OrderEventBus] = None
        self._event_handlers: Optional[EventHandlerRegistry] = None
        
        # Data
        self._data_validator: Optional[DataValidator] = None
        self._data_cache: Optional[DataCache] = None
        self._data_pipeline: Optional[MarketDataPipeline] = None
        
        # Risk
        self._limits_tracker: Optional[PersistentLimitsTracker] = None
        self._position_sizer: Optional[NotionalPositionSizer] = None
        self._risk_gate: Optional[PreTradeRiskGate] = None
        self._risk_manager: Optional[RiskManager] = None
        self._protections: Optional[ProtectionManager] = None  # ALL protections centralized here
        
        # Strategies
        self._strategy_registry: Optional[StrategyRegistry] = None
        self._strategy_lifecycle: Optional[StrategyLifecycleManager] = None
        
        # Broker
        self._broker_connector = None
        self._reconciler: Optional[BrokerReconciler] = None
        self._execution_engine: Optional[OrderExecutionEngine] = None  # NEW
        
        # NEW: Market and Real-time (initialized when broker is set)
        self._symbol_props_cache: Optional[SymbolPropertiesCache] = None
        self._security_cache: Optional[SecurityCache] = None
        self._user_stream: Optional[UserStreamTracker] = None

    def initialize(self, config_path: str) -> None:
        """
        Initialize all components in correct dependency order.
        
        ORDER (with new features):
        1. Config
        2. Clock (NEW - no dependencies)
        3. Throttler (NEW - no dependencies)
        4. State (OrderMachine, PositionStore, TransactionLog, OrderTracker)
        5. Events
        6. Data
        7. Risk (including NEW Protections)
        8. Strategies
        9. Broker (set later via set_broker_connector)
        10. Market/Real-time (set later when broker available)
        """
        if self._config is not None:
            raise RuntimeError("Container already initialized")
        
        # 1. Load config
        loader = ConfigLoader(config_dir=Path(config_path).parent)
        raw_config = loader.load()
        self._config = TradingConfig(**raw_config)
        logger.info("Config loaded")
        
        # 2. NEW: Initialize clock (injectable time)
        self._clock = get_clock(raw_config)
        logger.info(f"Clock initialized: {type(self._clock).__name__}")
        
        # 3. NEW: Initialize throttler (rate limiting)
        self._throttler = create_combined_throttler()
        logger.info("Throttler initialized with combined rate limits")
        
        # 4. Initialize state components (now with clock)
        self._transaction_log = TransactionLog(
            log_path=self._config.transaction_log_path,
            clock=self._clock  # CRITICAL: Pass clock for backtest-safe timestamps
        )
        self._event_bus = OrderEventBus()
        
        self._order_machine = OrderStateMachine(
            event_bus=self._event_bus,
            transaction_log=self._transaction_log
        )
        self._position_store = PositionStore(
            db_path=self._config.position_db_path,
            clock=self._clock  # CRITICAL: Pass clock for backtest-safe timestamps
        )
        
        # NEW: Order tracker for fill tracking
        self._order_tracker = OrderTracker()
        logger.info("OrderTracker initialized")
        
        # 5. Initialize event handlers
        self._event_handlers = EventHandlerRegistry(
            order_machine=self._order_machine,
            position_store=self._position_store,
            transaction_log=self._transaction_log
        )
        
        # 6. Initialize data components
        self._data_validator = DataValidator(
            max_staleness_seconds=self._config.data.max_staleness_seconds,
            require_complete_bars=True
        )
        self._data_cache = DataCache()
        self._data_pipeline = MarketDataPipeline(
            alpaca_api_key=self._config.broker.api_key,
            alpaca_api_secret=self._config.broker.api_secret,
            max_staleness_seconds=self._config.data.max_staleness_seconds
        )
        
        # 7. Initialize risk components
        self._limits_tracker = PersistentLimitsTracker(
            db_path=str(self._config.limit_tracker_path),
            daily_loss_limit=self._config.risk.daily_loss_limit_usd
        )
        self._position_sizer = NotionalPositionSizer(
            max_exposure_per_position=self._config.risk.max_position_size_pct / Decimal("100")
        )
        
        # NEW: Protections (unified circuit breakers + guardrails) - CREATE BEFORE RISK GATE
        from core.risk.protections import create_default_protections
        self._protections = create_default_protections()
        logger.info("ProtectionManager initialized: 5 protections active (TimeWindow, Volatility, Stoploss, Drawdown, Cooldown)")
        
        self._risk_gate = PreTradeRiskGate(
            limits_tracker=self._limits_tracker,
            position_sizer=self._position_sizer,
            account_value=self._config.risk.initial_account_value,
            enable_pdt_protection=True,
            max_orders_per_day=self._config.session.max_daily_trades,
            protections=self._protections  # NEW: Pass protections
        )
        
        risk_limits = RiskLimits(
            max_position_size_usd=self._config.risk.initial_account_value * self._config.risk.max_position_size_pct / Decimal("100"),
            max_position_pct_portfolio=self._config.risk.max_position_size_pct / Decimal("100"),
            max_positions=self._config.risk.max_open_positions,
            max_daily_loss_usd=self._config.risk.daily_loss_limit_usd,
            max_daily_loss_pct=Decimal("0.02")
        )
        
        self._risk_manager = RiskManager(
            position_store=self._position_store,
            limits=risk_limits
        )
        
        # 8. Initialize strategy components
        self._strategy_registry = StrategyRegistry()
        self._strategy_lifecycle = StrategyLifecycleManager()
        
        logger.info("Container initialized (6 new features integrated)")
        logger.info("Call set_broker_connector() to complete market/realtime initialization")

    # ========================================================================
    # COMPONENT ACCESSORS
    # ========================================================================
    
    def get_config(self) -> TradingConfig:
        if self._config is None:
            raise RuntimeError("Container not initialized")
        return self._config
    
    # NEW: Clock accessor
    def get_clock(self) -> Clock:
        if self._clock is None:
            raise RuntimeError("Container not initialized")
        return self._clock
    
    # NEW: Throttler accessor
    def get_throttler(self) -> Throttler:
        if self._throttler is None:
            raise RuntimeError("Container not initialized")
        return self._throttler
    
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
    
    # NEW: OrderTracker accessor
    def get_order_tracker(self) -> OrderTracker:
        if self._order_tracker is None:
            raise RuntimeError("Container not initialized")
        return self._order_tracker
    
    def get_event_bus(self) -> OrderEventBus:
        if self._event_bus is None:
            raise RuntimeError("Container not initialized")
        return self._event_bus
    
    def get_data_validator(self) -> DataValidator:
        if self._data_validator is None:
            raise RuntimeError("Container not initialized")
        return self._data_validator
    
    def get_data_pipeline(self) -> MarketDataPipeline:
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
    
    # NEW: Protections accessor
    def get_protections(self) -> ProtectionManager:
        if self._protections is None:
            raise RuntimeError("Container not initialized")
        return self._protections
    
    def get_strategy_registry(self) -> StrategyRegistry:
        if self._strategy_registry is None:
            raise RuntimeError("Container not initialized")
        return self._strategy_registry
    
    def get_strategy_lifecycle(self) -> StrategyLifecycleManager:
        if self._strategy_lifecycle is None:
            raise RuntimeError("Container not initialized")
        return self._strategy_lifecycle
    
    def get_reconciler(self):
        """
        Startup reconciler: compares broker state vs local state.
        """
        if getattr(self, "_reconciler", None) is None:
            self._reconciler = StartupReconciler(
                broker=self._broker,
                position_store=self.get_position_store(),
                order_tracker=self.get_order_tracker(),
            )
        return self._reconciler
    
    # NEW: Order Execution Engine accessor
    def get_order_execution_engine(self) -> Optional[OrderExecutionEngine]:
        return self._execution_engine
    
    # NEW: Symbol Properties accessor
    def get_symbol_properties_cache(self) -> Optional[SymbolPropertiesCache]:
        return self._symbol_props_cache
    
    # NEW: Security Cache accessor
    def get_security_cache(self) -> Optional[SecurityCache]:
        return self._security_cache
    
    # NEW: UserStream accessor
    def get_user_stream(self) -> Optional[UserStreamTracker]:
        return self._user_stream

    # ========================================================================
    # BROKER & MARKET INITIALIZATION
    # ========================================================================
    
    def set_broker_connector(self, connector) -> None:
        """
        Set broker connector and initialize market/realtime features.
        
        This completes the initialization by setting up:
        1. Broker reconciler
        2. Symbol properties cache (NEW)
        3. Security cache (NEW)
        4. User stream tracker (NEW)
        """
        self._broker_connector = connector
        
        # Create reconciler
        if self._position_store and self._order_machine:
            self._reconciler = BrokerReconciler(
                broker_connector=connector,
                position_store=self._position_store,
                order_machine=self._order_machine
            )
            logger.info("Broker reconciler created")
        
        # NEW: Initialize symbol properties cache
        self._symbol_props_cache = SymbolPropertiesCache(connector)
        logger.info("Symbol properties cache initialized")
        
        # NEW: Initialize security cache
        self._security_cache = SecurityCache(self._symbol_props_cache)
        logger.info("Security cache initialized")
        
        # NEW: Initialize order execution engine
        if self._order_machine and self._position_store:
            self._execution_engine = OrderExecutionEngine(
                broker=connector,
                state_machine=self._order_machine,
                position_store=self._position_store,
                symbol_properties=self._symbol_props_cache,  # Pass symbol properties for validation
                order_tracker=self._order_tracker  # NEW: Pass order tracker for fill tracking
            )
            logger.info("Order execution engine initialized with symbol properties + order tracker")
        
        # NEW: Initialize user stream tracker
        if self._config:
            self._user_stream = UserStreamTracker(
                api_key=self._config.broker.api_key,
                api_secret=self._config.broker.api_secret,
                is_paper=self._config.broker.paper_trading  # FIXED: was .account.mode
            )
            
            # Wire user stream to order tracker
            self._user_stream.on_trade_update(self._handle_trade_update)
            self._user_stream.on_account_update(self._handle_account_update)
            
            logger.info(f"User stream tracker initialized (paper={self._config.broker.paper_trading})")
    
    async def _handle_trade_update(self, update: dict):
        """
        Handle trade update from WebSocket.
        
        Feeds fills to OrderTracker for lifecycle tracking.
        """
        event = update.get('event')
        order_data = update.get('order', {})
        client_order_id = order_data.get('client_order_id')
        
        if not client_order_id:
            return
        
        # Process fills
        if event in ('fill', 'partial_fill'):
            fill = FillEvent(
                timestamp=self._clock.now(),
                quantity=Decimal(str(order_data.get('filled_qty', 0))),
                price=Decimal(str(order_data.get('filled_avg_price', 0))),
                commission=Decimal('0')  # Calculate from order if available
            )
            self._order_tracker.process_fill(client_order_id, fill)
            logger.info(f"Processed fill for order {client_order_id}")
        
        # Process other status updates
        else:
            self._order_tracker.process_order_update(client_order_id, {
                'status': order_data.get('status'),
                'exchange_order_id': order_data.get('id'),
                'filled_qty': order_data.get('filled_qty')
            })
    
    async def _handle_account_update(self, update: dict):
        """Handle account update from WebSocket"""
        logger.info(
            "Account update: cash=%s, buying_power=%s",
            update.get('cash'),
            update.get('buying_power')
        )
    
    # ========================================================================
    # LIFECYCLE
    # ========================================================================
    
    def start(self) -> None:
        """Start all startable components including NEW user stream"""
        if self._event_bus:
            self._event_bus.start()
        
        logger.info("Container started")
    
    async def start_async(self) -> None:
        """Start async components (user stream)"""
        if self._user_stream:
            await self._user_stream.start()
            logger.info("User stream started")
    
    def stop(self) -> None:
        """Stop all stoppable components"""
        if self._event_bus:
            try:
                self._event_bus.stop(timeout=5.0)
            except Exception as e:
                logger.error(f"Error stopping event bus: {e}")
        
        logger.info("Container stopped")
    
    async def stop_async(self) -> None:
        """Stop async components (user stream)"""
        if self._user_stream:
            try:
                await self._user_stream.stop()
                logger.info("User stream stopped")
            except Exception as e:
                logger.error(f"Error stopping user stream: {e}")

    def init_from_file(self, path):
        """
        Backwards-compatible wrapper.
        Redirects to the actual config-loading method used in this repo.
        """
        # Try the modern loader first
        if hasattr(self, "load_config") and callable(self.load_config):
            return self.load_config(path)

        if hasattr(self, "initialize") and callable(self.initialize):
            return self.initialize(path)

        if hasattr(self, "from_file") and callable(self.from_file):
            return self.from_file(path)

        raise NotImplementedError(
            "Container.init_from_file() was called but no compatible loader exists. "
            "Inspect container.py to wire the correct method."
        )
