# MiniQuantDesk v2

**Industrial-Grade Algorithmic Trading System**

A production-ready, deterministic, event-driven algorithmic trading platform designed for small accounts ($200-$1500), built from the ground up with institutional-level engineering practices.

## 🎯 Design Principles

- **Deterministic** - No race conditions, no ambiguous state
- **Event-Driven** - Real-time order updates via WebSocket, no polling
- **Crash-Recoverable** - SQLite-backed state, automatic reconciliation on startup
- **Broker-Synced** - Continuous reconciliation prevents position drift
- **Schema-Validated** - Pydantic config validation, fails fast on errors
- **Small-Account-Safe** - Notional-based sizing, fractional shares, account validation
- **ML-Ready** - Structured JSON logging for training data collection

## 🏗️ Architecture

Based on best practices from:
- **LEAN** (QuantConnect) - Deterministic event sequencing, order state machine
- **Freqtrade** - Configuration discipline, schema validation
- **Hummingbot** - Broker connector abstraction, clean interfaces

### Core Components

```
core/
├── logging/       # Structured logging (JSON, correlation IDs, multiple streams)
├── events/        # Event bus for order lifecycle
├── state/         # Order state machine, position store, transaction log
├── data/          # Market data contracts, providers, validators
├── risk/          # Pre-trade risk gate, position sizing, limits
├── config/        # Pydantic configuration schema
└── di/            # Dependency injection container
```

## 📋 Status: Foundation Phase (Week 1)

### ✅ Completed

1. **Logging Infrastructure**
   - Structured JSON logging for ML/AI consumption
   - Multiple log streams (system, trading, orders, positions, risk, data, performance)
   - Correlation ID tracking for request tracing
   - Automatic performance timing
   - Log rotation and compression
   - Thread-safe operation

2. **Configuration System**
   - Pydantic schema validation
   - Single source of truth (config.yaml)
   - Secrets management (.env.local)
   - Small account validation
   - Fails fast on invalid config

3. **Project Structure**
   - 36 directories created
   - 77 files structured
   - Testing infrastructure prepared

### 🚧 In Progress

Week 1 Remaining:
- [ ] OrderStateMachine with transition guards
- [ ] OrderEventBus (thread-safe queue)
- [ ] PositionStore (SQLite with WAL mode)
- [ ] TransactionLog (append-only event log)

## 🚀 Quick Start

### Prerequisites

```bash
# Python 3.10+
python --version

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. **Copy secrets template:**
   ```bash
   cp config/.env.local.template config/.env.local
   ```

2. **Add your credentials to config/.env.local:**
   ```bash
   BROKER_API_KEY=your_alpaca_key
   BROKER_API_SECRET=your_alpaca_secret
   PAPER_TRADING=true
   ```

3. **Review config/config.yaml:**
   - Adjust risk parameters for your account size
   - Configure strategies
   - Set logging preferences

### Test Foundation Systems

```bash
cd C:\Users\Zacha\Desktop\MiniQuantDeskv2
python scripts\test_foundation.py
```

This validates:
- ✅ Logging system works
- ✅ Configuration loads and validates
- ✅ Log streams are properly separated
- ✅ JSON formatting is correct
- ✅ Correlation ID tracking works

### Log Output

```
logs/
├── system/         # Infrastructure events
├── trading/        # Strategy signals and decisions
├── orders/         # Order lifecycle (ML training data)├── positions/      # Position changes
├── risk/           # Risk decisions and gate checks
├── data/           # Data quality and staleness issues
└── performance/    # Execution timing and profiling
```

Each stream outputs:
- **JSON format** for programmatic analysis (ML/AI training)
- **Human-readable console** for development
- **Automatic rotation** when files exceed 10MB

### Example JSON Log Entry

```json
{
  "timestamp": "2025-01-19T18:30:45.123456Z",
  "level": "INFO",
  "logger": "miniquantdesk.orders",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Order submitted",
  "extra": {
    "order_id": "ORD_001",
    "symbol": "SPY",
    "quantity": 1,
    "side": "LONG",
    "entry_price": "598.45"
  }
}
```

## 📐 Configuration Schema

### Risk Management

```yaml
risk:
  max_open_positions: 1              # Simultaneous positions
  max_position_size_pct: 10.0        # % of account per position
  daily_loss_limit_usd: 50.0         # Daily loss limit
  weekly_loss_limit_usd: 150.0       # Weekly loss limit
  risk_per_trade_pct: 1.0            # Risk per trade
  circuit_breaker_enabled: true
  circuit_breaker_loss_pct: 5.0
  halt_duration_minutes: 30
```

### Broker Connection

```yaml
broker:
  name: alpaca
  api_key: YOUR_API_KEY               # Override in .env.local
  api_secret: YOUR_API_SECRET         # Override in .env.local
  base_url: https://paper-api.alpaca.markets
  paper_trading: true                 # CRITICAL: false = LIVE MONEY
```

### Strategy Configuration

```yaml
strategies:
  - name: VWAPMeanReversionStrategy
    enabled: true
    symbols:
      - SPHD                          # Low-price ETF for small accounts
    timeframe: 1Min
    lookback_bars: 50
    parameters:
      vwap_window: 20
      entry_threshold: 0.02
      stop_loss_pct: 0.02
      take_profit_pct: 0.01
```

## 🔒 Safety Features

### Configuration Validation

- **Pydantic schema** validates all parameters on load
- **Small account warnings** detect untradeable symbols
- **Secrets scrubbing** prevents credential leakage in logs
- **Fails fast** on invalid configuration

### Logging Safety

- **Correlation IDs** trace order lifecycle across components
- **Structured JSON** ensures machine-readable audit trail
- **Multiple streams** separate concerns for debugging
- **Automatic rotation** prevents disk space exhaustion

### Examples

```python
from core.logging import get_logger, LogContext, LogStream
from core.config import load_config

# Load and validate config
config = load_config()

# Initialize logging
setup_logging(
    log_dir=config.logging.log_dir,
    log_level=config.logging.log_level.value,
    json_logs=config.logging.json_logs
)

# Get stream-specific logger
logger = get_logger(LogStream.ORDERS)

# Log with correlation ID
with LogContext("order_12345"):
    logger.info(
        "Order submitted",
        extra={
            "order_id": "12345",
            "symbol": "SPY",
            "quantity": 1
        }
    )
```

## 📊 Log Streams Explained

| Stream | Purpose | ML/AI Use Case |
|--------|---------|----------------|
| **system** | Infrastructure events (startup, shutdown, errors) | System health monitoring |
| **trading** | Strategy signals and decisions | Strategy performance analysis |
| **orders** | Order lifecycle (submit, fill, cancel) | Trade execution quality |
| **positions** | Position changes (open, close, updates) | Position management analysis |
| **risk** | Risk decisions (approve/reject) | Risk model validation |
| **data** | Data quality issues (staleness, gaps) | Data pipeline monitoring |
| **performance** | Execution timing and profiling | Performance optimization |

## 🛠️ Development Roadmap

### Week 1: Foundation ✅ (In Progress)
- [x] Logging infrastructure
- [x] Configuration system
- [ ] OrderStateMachine
- [ ] OrderEventBus
- [ ] PositionStore
- [ ] TransactionLog

### Week 2: Connectors
- [ ] BrokerConnector interface
- [ ] AlpacaConnector
- [ ] AlpacaStreamHandler (WebSocket)
- [ ] MockBrokerConnector

### Week 3: Data Pipeline
- [ ] MarketDataContract
- [ ] MarketDataProvider interface
- [ ] DataValidator
- [ ] Provider mappers

### Week 4: Risk & Execution
- [ ] PreTradeRiskGate
- [ ] PersistentLimitTracker
- [ ] NotionalPositionSizer
- [ ] EventDrivenOrderManager

### Week 5: Strategy & Orchestration
- [ ] IStrategy interface
- [ ] VWAP mean reversion strategy
- [ ] StrategyRegistry
- [ ] TradeCycleOrchestrator

### Week 6: Session & Recovery
- [ ] StartupRecoveryProtocol
- [ ] SessionManager
- [ ] DI container
- [ ] main.py entry point

### Week 7: Testing
- [ ] Unit tests (90%+ coverage)
- [ ] Integration tests
- [ ] Parallel run validation

### Week 8: Deployment
- [ ] Paper trading validation ($200 account)
- [ ] Crash recovery testing
- [ ] Live deployment (paper mode)

## 📚 Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System architecture
- [MIGRATION.md](docs/MIGRATION.md) - v1 → v2 migration guide
- [STATE_MACHINE.md](docs/STATE_MACHINE.md) - Order state transitions
- [RECOVERY.md](docs/RECOVERY.md) - Crash recovery protocol
- [CONFIGURATION.md](docs/CONFIGURATION.md) - Config reference
- [TESTING.md](docs/TESTING.md) - Testing guide

## ⚠️ Critical Safety Rules

1. **NEVER commit .env.local** - Contains API credentials
2. **ALWAYS verify PAPER_TRADING=true** before deployment
3. **Test crash recovery** before live trading
4. **Validate small account** mode before trading
5. **Monitor logs** for all trading sessions

## 🤝 Contributing

This is a personal trading system. Not accepting external contributions.

## 📝 License

Proprietary - For personal use only

## 🔗 Resources

- [Alpaca API Docs](https://alpaca.markets/docs/)
- [LEAN Documentation](https://www.quantconnect.com/docs/)
- [Freqtrade Docs](https://www.freqtrade.io/en/stable/)
- [Pydantic Documentation](https://docs.pydantic.dev/)

---

**Status:** Foundation phase complete. Core infrastructure operational.
**Next:** Build OrderStateMachine and event bus (Week 1 completion)
