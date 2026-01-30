# MINIQUANTDESK SYSTEM ARCHITECTURE GUIDE
## Complete Overview - How Everything Works

**Date:** January 20, 2026
**Author:** Technical Documentation
**Purpose:** Comprehensive system explanation for operators

---

## TABLE OF CONTENTS

1. [High-Level Overview](#high-level-overview)
2. [System Architecture](#system-architecture)
3. [Data Flow](#data-flow)
4. [Component Details](#component-details)
5. [Phase Progression](#phase-progression)
6. [Safety Systems](#safety-systems)
7. [File Organization](#file-organization)

---

## HIGH-LEVEL OVERVIEW

### What is MiniQuantDesk?

MiniQuantDesk (MQD) is a **professional-grade algorithmic trading system** that:
- Runs automated trading strategies
- Manages risk across multiple layers
- Executes trades via Alpaca broker
- Monitors positions and P&L in real-time
- Notifies you via Discord
- Logs everything for audit and recovery

### Current State: Phase 1 "Rifleman Core"

**What Phase 1 Does:**
- Backtests strategies on historical data
- Executes one strategy at a time in paper/live trading
- Enforces strict risk limits (daily loss, position size)
- Tracks positions and calculates P&L
- Reconciles state with broker on every restart
- Sends detailed notifications to Discord

**What Phase 1 Does NOT Do (Yet):**
- Multiple concurrent strategies (Phase 2)
- Advanced order types beyond MARKET (Phase 2)
- AI/ML strategy selection (Phase 3)
- Portfolio optimization (Phase 2)

---

## SYSTEM ARCHITECTURE

### Layer Structure (Bottom to Top)

```
┌─────────────────────────────────────────────────────────────┐
│  MONITORING LAYER                                           │
│  - Discord notifications (7 channels)                       │
│  - Heartbeat monitoring                                     │
│  - Performance metrics                                      │
│  - Transaction logging                                      │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  STRATEGY LAYER                                             │
│  - Strategy registry (loads strategies)                     │
│  - Strategy lifecycle (on_init, on_bar, on_fill)           │
│  - VWAPMeanReversion strategy (example)                    │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  EXECUTION LAYER                                            │
│  - Order creation (from strategy signals)                   │
│  - Risk gate validation (BEFORE submission)                │
│  - Order state machine (tracks order lifecycle)            │
│  - Position tracking (updates on fills)                    │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  EVENT LAYER                                                │
│  - Event bus (thread-safe queue)                           │
│  - Event handlers (respond to fills, cancels, etc)         │
│  - Transaction log (persists all events)                   │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                 │
│  - Market data pipeline (OHLCV candles)                    │
│  - Data validation (staleness, gaps)                       │
│  - Data cache (reduces API calls)                          │
│  - Multiple providers (Polygon, Finnhub, FMP)             │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  BROKER LAYER                                               │
│  - Alpaca connector (paper & live)                         │
│  - Order submission                                         │
│  - Position queries                                         │
│  - Fill notifications                                       │
└─────────────────────────────────────────────────────────────┘
```

### Key Principle: **Defense in Depth**

Every dangerous operation has **multiple safety checks**:
1. **Data validation** - Is the market data good?
2. **Risk gate** - Does this trade violate limits?
3. **Order state machine** - Is this transition valid?
4. **Broker reconciliation** - Does our state match broker?
5. **Transaction log** - Can we recover if we crash?

---

## DATA FLOW

### Complete Trade Lifecycle

```
1. MARKET DATA ARRIVES
   ├─ Polygon API sends new 1-min candle for SPY
   ├─ DataValidator checks: Is it fresh? Any gaps?
   ├─ DataCache stores it (avoids re-fetching)
   └─ DataPipeline emits MarketDataReceivedEvent
              ▼
              
2. STRATEGY RECEIVES DATA
   ├─ VWAPMeanReversion.on_bar(bar) called
   ├─ Strategy calculates: price vs VWAP
   ├─ Decision: BUY signal (price below VWAP)
   └─ Returns TradingSignal object
              ▼
              
3. RISK GATE VALIDATION
   ├─ PreTradeRiskGate receives signal
   ├─ Checks:
   │   ├─ Daily loss limit not breached?
   │   ├─ Position size within limits?
   │   ├─ PDT protection (day trades remaining)?
   │   ├─ Max orders per day not exceeded?
   │   └─ Duplicate order check
   ├─ Decision: APPROVED ✓
   └─ Signal passed to execution
              ▼
              
4. ORDER CREATION
   ├─ OrderStateMachine.create_order()
   ├─ Order ID: ORD_20260120_001
   ├─ State: PENDING
   ├─ Stored in memory
   └─ Event: OrderCreatedEvent emitted
              ▼
              
5. BROKER SUBMISSION
   ├─ AlpacaConnector.submit_order()
   ├─ Alpaca returns: broker_order_id = "abc123"
   ├─ State transition: PENDING → SUBMITTED
   ├─ OrderStateMachine updates order object
   └─ Event: OrderSubmittedEvent emitted
              ▼
              
6. ORDER FILLED (at broker)
   ├─ Alpaca sends fill notification
   ├─ Filled: 10 shares @ $450.50
   ├─ OrderFilledEvent created
   └─ Event emitted to EventBus
              ▼
              
7. EVENT HANDLERS RESPOND
   ├─ EventHandler receives OrderFilledEvent
   ├─ Actions:
   │   ├─ OrderStateMachine: SUBMITTED → FILLED
   │   ├─ PositionStore: Add 10 shares SPY @ $450.50
   │   ├─ TransactionLog: Persist fill event
   │   └─ Discord: Send notification to #paper-trading
   └─ Strategy.on_order_filled() called
              ▼
              
8. POSITION TRACKING
   ├─ PositionStore now has:
   │   ├─ Symbol: SPY
   │   ├─ Quantity: 10
   │   ├─ Entry Price: $450.50
   │   ├─ Current Value: 10 × $452.00 = $4,520
   │   └─ Unrealized P&L: +$15.00
   └─ Position monitored for exit signals
              ▼
              
9. EXIT SIGNAL (later)
   ├─ Strategy detects: price crossed above VWAP
   ├─ Signal: SELL 10 shares SPY
   ├─ Risk gate approves
   ├─ Order submitted → filled
   ├─ Position closed
   ├─ Realized P&L: +$15.00 (logged)
   └─ Discord notification sent
```

---

## COMPONENT DETAILS

### 1. DATA LAYER

#### MarketDataContract
**File:** `core/data/contract.py`
**Purpose:** Define and validate OHLCV bar structure

```python
@dataclass
class OHLCVBar:
    symbol: str
    timestamp: datetime  # Always UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    
# Validation:
- OHLC prices must be positive
- High >= Low
- Timestamp must be timezone-aware
- No NaN or None values
```

#### DataValidator
**File:** `core/data/validator.py`
**Purpose:** Detect stale or bad market data

**Checks:**
- **Staleness:** Data < 90 seconds old?
- **Gaps:** Missing bars in sequence?
- **OHLC logic:** High >= Low, prices positive?

**Why Critical:** Bad data → bad signals → bad trades

#### DataCache
**File:** `core/data/cache.py`
**Purpose:** Reduce API calls, speed up backtests

**How it works:**
- LRU cache with max 10,000 bars
- TTL: 5 minutes
- Key: (symbol, timestamp, interval)
- Avoids re-fetching same bar multiple times

#### DataPipeline
**File:** `core/data/pipeline.py`
**Purpose:** Orchestrate data fetching → validation → caching

**Providers (in fallback order):**
1. Polygon.io (primary)
2. Finnhub (backup)
3. Financial Modeling Prep (backup)
4. Alpha Vantage (last resort)

---

### 2. RISK LAYER

#### PersistentLimitsTracker
**File:** `core/risk/limits.py`
**Purpose:** Track daily P&L and enforce loss limits

**Database:** `data/limits.db` (SQLite)
**Resets:** Daily at midnight UTC

**Limits enforced:**
- Daily loss limit: $500 (configurable)
- Max position size: $1,000 per symbol
- Max total exposure: $50,000

**Stop-ship:** When daily loss limit hit, NO new orders allowed

#### NotionalPositionSizer
**File:** `core/risk/sizing.py`
**Purpose:** Calculate safe position size

**Algorithm:**
```python
# Account: $10,000
# Max exposure per position: 20%
# Price: $450

max_notional = $10,000 × 0.20 = $2,000
max_shares = $2,000 / $450 = 4.44 → 4 shares

# Also considers:
- Existing positions
- Total portfolio exposure
- Min position value ($100)
```

#### PreTradeRiskGate
**File:** `core/risk/gate.py`
**Purpose:** Final approval before EVERY trade

**Rejection reasons:**
1. Daily loss limit breached
2. Position too large
3. PDT violation (> 3 day trades in 5 days)
4. Max orders per day exceeded
5. Duplicate order detected

**CRITICAL:** Order never reaches broker if rejected

---

### 3. EVENT LAYER

#### OrderEventBus
**File:** `core/events/bus.py`
**Purpose:** Thread-safe event distribution

**How it works:**
1. Events added to queue
2. Background thread processes queue
3. Handlers called for each event
4. Handler failures isolated (don't crash system)

**Thread-safe:** Producers/consumers can run concurrently

#### EventHandlerRegistry
**File:** `core/events/handlers.py`
**Purpose:** React to order lifecycle events

**Handlers:**
- `OrderFilledEvent` → Update position, log transaction
- `OrderPartiallyFilledEvent` → Update fill quantity
- `OrderCancelledEvent` → Log cancellation
- `OrderRejectedEvent` → Log rejection reason
- `PositionClosedEvent` → Record realized P&L
- `RiskLimitBreachedEvent` → Critical alert
- `KillSwitchActivatedEvent` → Emergency shutdown

#### TransactionLog
**File:** `core/state/transaction_log.py`
**Purpose:** Persist ALL state changes

**Format:** JSONL (JSON Lines)
**Location:** `logs/transactions.jsonl`

**Every line is an event:**
```json
{"event_type":"order_filled","timestamp":"2026-01-20T14:30:00Z","order_id":"ORD_001",...}
{"event_type":"position_opened","timestamp":"2026-01-20T14:30:01Z","symbol":"SPY",...}
```

**Why critical:** 
- Crash recovery
- Audit trail
- Debugging
- Compliance

---

### 4. STATE LAYER

#### OrderStateMachine
**File:** `core/state/order_machine.py`
**Purpose:** Track order lifecycle with validated transitions

**States:**
```
PENDING → Created locally, not yet at broker
SUBMITTED → Sent to broker, acknowledged
PARTIALLY_FILLED → Some shares filled
FILLED → All shares filled (TERMINAL)
CANCELLED → User or system cancelled (TERMINAL)
REJECTED → Broker or risk gate rejected (TERMINAL)
EXPIRED → Timed out (TERMINAL)
```

**Guarantees:**
- Only valid transitions allowed
- Broker confirmation required for broker transitions
- All transitions logged atomically
- Terminal states cannot transition further
- Thread-safe

**Storage:**
- Stores Order objects in memory
- `get_order(order_id)` - Retrieve order
- `get_pending_orders()` - Get non-terminal orders
- `transition()` - Validate and execute state change

#### PositionStore
**File:** `core/state/position_store.py`
**Purpose:** Track open positions

**Database:** `data/positions.db` (SQLite)

**Operations:**
- `open_position()` - Create new position
- `update_position()` - Modify quantity/price
- `close_position()` - Mark as closed, calculate P&L
- `get_position(symbol)` - Retrieve current position
- `get_all_positions()` - Get all open positions

**P&L Calculation:**
```python
# Entry: 10 shares @ $450.00
# Exit: 10 shares @ $452.00
realized_pnl = (exit_price - entry_price) × quantity
             = ($452.00 - $450.00) × 10
             = $20.00
```

#### BrokerReconciler
**File:** `core/state/reconciler.py`
**Purpose:** Sync local state with broker truth

**When:** EVERY startup (before trading)

**Reconciliation steps:**
1. Fetch broker positions
2. Fetch local positions
3. Compare symbol-by-symbol
4. Resolve discrepancies:
   - Missing position → Add locally
   - Extra position → Remove locally
   - Quantity mismatch → Update to broker value

5. Fetch broker pending orders
6. Fetch local pending orders
7. Compare order-by-order
8. Resolve discrepancies:
   - Extra local order → Cancel it

**Why critical:** Prevents state desync after crashes/restarts

---

### 5. STRATEGY LAYER

#### StrategyBase
**File:** `strategies/base.py`
**Purpose:** Abstract base for all strategies

**Interface:**
```python
class IStrategy:
    def on_init(self, context) -> None:
        """Called once at startup"""
        
    def on_bar(self, bar: OHLCVBar) -> Optional[TradingSignal]:
        """Called on each new bar - MAIN LOGIC HERE"""
        
    def on_order_filled(self, order: Order) -> None:
        """Called when order fills"""
        
    def on_position_closed(self, position: Position) -> None:
        """Called when position closes"""
```

#### StrategyRegistry
**File:** `strategies/registry.py`
**Purpose:** Factory pattern for strategy instantiation

**Usage:**
```python
registry = StrategyRegistry()
registry.register("vwap_mean_reversion", VWAPMeanReversion)

# Later:
strategy = registry.create("vwap_mean_reversion", params={...})
```

#### VWAPMeanReversion (Example Strategy)
**File:** `strategies/vwap_mean_reversion.py`
**Purpose:** Trade mean reversion to VWAP

**Logic:**
1. Calculate VWAP over lookback period (default: 20 bars)
2. If price < VWAP × (1 - threshold) → BUY signal
3. If price > VWAP × (1 + threshold) → SELL signal
4. Stop loss: entry ± 2%
5. Take profit: entry ± 1.5%

**Parameters:**
- lookback_period: 20
- entry_threshold: 0.02 (2%)
- stop_loss_pct: 0.02 (2%)
- take_profit_pct: 0.015 (1.5%)

---

### 6. EXECUTION LAYER

#### Container (Dependency Injection)
**File:** `core/di/container.py`
**Purpose:** Wire all components together

**Initialization order:**
1. Config (loads from YAML/env)
2. State (OrderMachine, PositionStore, TransactionLog)
3. Events (EventBus, Handlers)
4. Data (Validator, Cache, Pipeline)
5. Risk (LimitsTracker, PositionSizer, RiskGate)
6. Strategies (Registry, Lifecycle)
7. Broker (Connector, Reconciler)

**Usage:**
```python
container = Container()
container.initialize(config_path="config/config.yaml")
container.start()

# Access components:
risk_gate = container.get_risk_gate()
position_store = container.get_position_store()
```

---

### 7. MONITORING LAYER

#### Discord Integration
**Files:** `core/discord/*.py`
**Purpose:** Real-time notifications

**7 Channels:**
1. **#paper-trading** - Paper trading activity
2. **#live-trading** - Live trading activity (future)
3. **#economic-calendar** - Economic events
4. **#scanner-results** - Market opportunities (Phase 2)
5. **#heartbeat** - System health pings
6. **#backtest-results** - Backtest performance
7. **#alerts** - Errors, warnings, risk breaches

**Notification types:**
- Order submitted/filled/cancelled
- Position opened/closed
- P&L updates
- Risk limit breaches
- System errors
- Heartbeat (every 5 minutes)

#### Logging System
**File:** `core/logging/*.py`
**Purpose:** Structured logging for debugging

**Log streams:**
- SYSTEM - Infrastructure logs
- ORDERS - Order lifecycle
- POSITIONS - Position tracking
- RISK - Risk gate decisions
- STRATEGY - Strategy signals
- DATA - Market data events

**Log locations:**
- `logs/system/YYYY-MM-DD.log`
- `logs/trading/YYYY-MM-DD.log`
- `logs/heartbeats/YYYY-MM-DD.log`

**Structured format:**
```json
{
  "timestamp": "2026-01-20T14:30:00Z",
  "level": "INFO",
  "logger": "ORDERS",
  "message": "Order filled",
  "order_id": "ORD_001",
  "symbol": "SPY",
  "quantity": "10",
  "fill_price": "450.50"
}
```

---

## PHASE PROGRESSION

### Phase 1: "Rifleman Core" (CURRENT - 95% Complete)

**Capabilities:**
- Single strategy execution
- Market orders only
- Paper and live trading
- Basic risk management
- Position tracking
- Discord notifications

**Status:** Ready for paper trading validation

### Phase 2: "Squad Upgrade" (Planned)

**Additions:**
- Multiple concurrent strategies
- Advanced order types (LIMIT, STOP, TRAILING)
- Smart execution (TWAP, VWAP)
- Market scanner
- Enhanced position sizing
- Multi-timeframe analysis

**Timeline:** 2-3 months after Phase 1 validated

### Phase 3: "Adaptive Platoon" (Future)

**Additions:**
- AI/ML strategy selection
- Alpha decay tracking
- Market regime classification
- Reinforcement learning
- Ensemble model voting
- Automated strategy optimization

**Timeline:** 6-12 months after Phase 2

### Phase 4: "Battalion Strength" (Long-term)

**Additions:**
- Tax optimization
- Multi-account management
- Cross-asset strategies
- High-frequency components
- Institutional-grade infrastructure

**Timeline:** 12+ months

---

## SAFETY SYSTEMS

### 1. Multi-Layer Risk Management

**Layer 1: Data Validation**
- Reject stale data (> 90 seconds old)
- Detect gaps in data
- Validate OHLC logic

**Layer 2: Risk Gate**
- Daily loss limits
- Position size limits
- PDT protection
- Duplicate order check

**Layer 3: Order State Machine**
- Only valid transitions
- Broker confirmation required
- Terminal states protected

**Layer 4: Broker Reconciliation**
- Startup sync with broker
- Resolve discrepancies
- Broker is always truth

**Layer 5: Transaction Log**
- All events persisted
- Crash recovery possible
- Full audit trail

### 2. Emergency Controls

#### Kill Switch
**Trigger:** Manual or automatic (risk breach)
**Actions:**
1. Cancel all pending orders
2. Close all positions (market orders)
3. Disable new order submission
4. Log incident to Discord #alerts

**Access:** Via TradingEngine.activate_kill_switch()

#### Daily Loss Limit
**When breached:**
1. All new orders blocked
2. Existing positions allowed to close
3. Critical alert sent to Discord
4. Automatically resets at midnight UTC

---

## FILE ORGANIZATION

```
MiniQuantDeskv2/
├── config/
│   ├── config.yaml           # Main configuration
│   ├── phase1.yaml          # Phase 1 specific config
│   └── symbols.yaml         # Trading symbols list
│
├── core/
│   ├── brokers/
│   │   └── alpaca_connector.py  # Alpaca API integration
│   ├── config/
│   │   ├── loader.py            # Config loading
│   │   └── schema.py            # Config validation
│   ├── data/
│   │   ├── contract.py          # OHLCV bar definition
│   │   ├── validator.py         # Data validation
│   │   ├── cache.py             # Data caching
│   │   └── pipeline.py          # Data orchestration
│   ├── di/
│   │   └── container.py         # Dependency injection
│   ├── discord/
│   │   ├── bot.py               # Discord bot
│   │   ├── notifier.py          # Notifications
│   │   └── integration.py       # Integration layer
│   ├── events/
│   │   ├── bus.py               # Event bus
│   │   ├── handlers.py          # Event handlers
│   │   └── types.py             # Event definitions
│   ├── execution/
│   │   └── engine.py            # Execution engine
│   ├── logging/
│   │   ├── logger.py            # Logger setup
│   │   └── formatters.py        # Log formatting
│   ├── risk/
│   │   ├── limits.py            # Limits tracker
│   │   ├── sizing.py            # Position sizer
│   │   └── gate.py              # Risk gate
│   └── state/
│       ├── order_machine.py     # Order state machine
│       ├── position_store.py    # Position tracking
│       ├── transaction_log.py   # Event persistence
│       └── reconciler.py        # Broker reconciliation
│
├── strategies/
│   ├── base.py                  # Strategy interface
│   ├── registry.py              # Strategy factory
│   ├── lifecycle.py             # Strategy lifecycle
│   └── vwap_mean_reversion.py   # Example strategy
│
├── data/                        # Runtime data (created)
│   ├── positions.db            # Position database
│   ├── limits.db               # Risk limits database
│   └── cache/                  # Market data cache
│
├── logs/                        # Log files (created)
│   ├── system/
│   ├── trading/
│   ├── heartbeats/
│   └── transactions.jsonl
│
├── _env                         # Environment variables (SECRET)
│
├── entry_paper.py               # Paper trading entry point
├── entry_live.py                # Live trading entry point (future)
│
└── tests/
    ├── test_integration_simple.py  # Integration tests
    └── ...
```

---

## HOW IT ALL WORKS TOGETHER

### Startup Sequence (Paper Trading)

```
1. entry_paper.py runs
   ├─ Load config from .env + config/config.yaml
   ├─ Initialize Container (DI)
   ├─ Create all components
   └─ Wire dependencies
   
2. Start Components
   ├─ EventBus.start() (background thread)
   ├─ RiskGate.start()
   ├─ Discord bot connects
   └─ Heartbeat monitor starts
   
3. Broker Connection
   ├─ AlpacaConnector.connect()
   ├─ Verify paper API keys
   └─ Test connection
   
4. Reconciliation (CRITICAL)
   ├─ BrokerReconciler.reconcile_startup()
   ├─ Sync positions with broker
   ├─ Sync pending orders with broker
   ├─ Log any discrepancies
   └─ Resolve conflicts
   
5. Load Strategy
   ├─ StrategyRegistry.create("vwap_mean_reversion")
   ├─ Strategy.on_init() called
   └─ Strategy ready
   
6. Main Trading Loop
   ├─ Wait for market open (9:30 ET)
   ├─ Start fetching 1-min bars
   ├─ For each bar:
   │   ├─ Validate data
   │   ├─ Call strategy.on_bar()
   │   ├─ If signal generated:
   │   │   ├─ Risk gate validation
   │   │   ├─ Create order
   │   │   ├─ Submit to broker
   │   │   └─ Track state
   │   └─ Update positions
   └─ Market close (16:00 ET)
   
7. Shutdown
   ├─ Close all positions (optional)
   ├─ Cancel pending orders
   ├─ Stop event bus
   ├─ Disconnect Discord
   ├─ Flush logs
   └─ Exit
```

---

## KEY CONCEPTS

### 1. Event-Driven Architecture
Everything is an event:
- New market data → MarketDataReceivedEvent
- Order filled → OrderFilledEvent
- Position closed → PositionClosedEvent
- Risk limit breached → RiskLimitBreachedEvent

**Benefits:**
- Decoupled components
- Easy to add new handlers
- Natural audit trail
- Replay events for debugging

### 2. State Machine Pattern
Orders follow strict state transitions:
- Prevents invalid states
- Enforces broker confirmation
- Logs all transitions
- Enables recovery

### 3. Dependency Injection
Container wires everything:
- Single source of truth for components
- Easy testing (swap components)
- Clear dependency graph
- Prevents circular dependencies

### 4. Broker Reconciliation
Always trust the broker:
- Our state might be wrong after crash
- Broker is source of truth
- Reconcile on EVERY startup
- Log and resolve discrepancies

### 5. Defense in Depth
Multiple safety layers:
- Data validation
- Risk gate
- Order state machine
- Broker reconciliation
- Transaction log

---

## SUMMARY

MiniQuantDesk is a **production-grade algorithmic trading system** with:
- 108,000+ lines of code
- 225+ files
- Institutional-quality architecture
- Multi-layer safety mechanisms
- Complete audit trail
- Crash recovery
- Real-time monitoring

**Current State:** Phase 1 complete, ready for paper trading validation

**Next Steps:** 
1. Run paper trading for 1-2 weeks
2. Validate all safety mechanisms
3. Performance profiling
4. Transition to live trading with small capital

**Long-term:** Progress through Phases 2-4 for advanced capabilities
