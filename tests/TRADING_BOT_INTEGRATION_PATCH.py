"""
TRADING BOT INTEGRATION PATCH - Universe System

OBJECTIVE:
Integrate trading bot (app.py) with two-gate universe system:
1. Process scanner candidates (Gate 2)
2. Load dynamic universe (CORE + accepted)
3. Trade on universe symbols

===============================================================================
OPTION 1: INTEGRATED MODE (Recommended)
===============================================================================

Gate 2 processing runs inside the trading bot main loop.

PATCH LOCATION: core/runtime/app.py

In run() function, after strategy initialization, add:

```python
    # ====================================================================
    # UNIVERSE SYSTEM INTEGRATION
    # ====================================================================
    from pathlib import Path as UnivPath
    from core.universe import UniverseInboxProcessor, get_universe_symbols
    
    # Initialize universe processor (Gate 2)
    universe_data_dir = UnivPath("data/universe")
    universe_processor = UniverseInboxProcessor(
        data_dir=universe_data_dir,
        clock=container.get_clock(),
        broker_connector=broker,
    )
    
    logger.info("Universe system enabled (two-gate mode)")
```

Then, in the main trading loop (while state.running), add:

```python
        # Process scanner candidates (Gate 2)
        # Check if we have open positions/orders
        positions = broker.get_positions()
        orders = broker.get_open_orders()
        has_open_position = len(positions) > 0
        has_open_orders = len(orders) > 0
        
        # Process new candidates from scanner
        decisions = universe_processor.process_new_candidates(
            has_open_position=has_open_position,
            has_open_orders=has_open_orders,
        )
        
        if decisions:
            for d in decisions:
                logger.info(
                    f"Universe decision: {d.symbol} {d.decision.upper()} "
                    f"({d.reason})"
                )
        
        # Load current universe
        all_symbols = get_universe_symbols(mode="hybrid")
        logger.info(f"Trading universe: {len(all_symbols)} symbols")
```

Replace the hardcoded symbol loop with universe-based loop:

BEFORE:
```python
        for symbol in all_symbols:  # Hardcoded list
            # Strategy logic...
```

AFTER:
```python
        # Load dynamic universe
        universe_symbols = get_universe_symbols(mode="hybrid")
        
        for symbol in universe_symbols:
            # Strategy logic...
```

===============================================================================
OPTION 2: DAEMON MODE (Separate Process)
===============================================================================

Run Gate 2 processing as a separate daemon process.

Step 1: Run daemon in separate terminal

```powershell
# Terminal 1: Gate 2 processor
python -m core.universe.daemon
```

Step 2: Patch trading bot to load universe only

```python
# In app.py, before main loop:
from core.universe import get_universe_symbols

logger.info("Universe loader enabled (daemon mode)")

# In main loop, replace hardcoded symbols:
universe_symbols = get_universe_symbols(mode="hybrid")

for symbol in universe_symbols:
    # Strategy logic...
```

===============================================================================
COMPLETE INTEGRATION EXAMPLE
===============================================================================

```python
# In core/runtime/app.py

def run(opts: RunOptions) -> None:
    container = Container()
    container.init_from_file(opts.config_path)
    
    # ... existing initialization ...
    
    # ================================================================
    # UNIVERSE SYSTEM
    # ================================================================
    from pathlib import Path as UnivPath
    from core.universe import UniverseInboxProcessor, get_universe_symbols
    
    universe_enabled = os.getenv("UNIVERSE_ENABLED", "1") == "1"
    universe_mode = os.getenv("UNIVERSE_MODE", "hybrid")  # hybrid|scanner|accepted
    
    universe_processor = None
    if universe_enabled:
        try:
            universe_data_dir = UnivPath(
                os.getenv("UNIVERSE_DATA_DIR", "data/universe")
            )
            universe_processor = UniverseInboxProcessor(
                data_dir=universe_data_dir,
                clock=container.get_clock(),
                broker_connector=broker,
            )
            logger.info(
                f"✓ Universe system enabled (mode={universe_mode})"
            )
        except Exception as e:
            logger.error(f"Universe system init failed: {e}")
            universe_enabled = False
    
    # ================================================================
    # MAIN LOOP
    # ================================================================
    
    while state.running:
        try:
            # Get account info
            acct = broker.get_account_info()
            
            # ========================================================
            # UNIVERSE PROCESSING (Gate 2)
            # ========================================================
            if universe_processor:
                # Check broker state
                positions = broker.get_positions()
                orders = broker.get_open_orders()
                has_open_position = len(positions) > 0
                has_open_orders = len(orders) > 0
                
                # Process new scanner candidates
                decisions = universe_processor.process_new_candidates(
                    has_open_position=has_open_position,
                    has_open_orders=has_open_orders,
                )
                
                # Log decisions
                for d in decisions:
                    if d.decision == "accept":
                        logger.info(
                            f"✓ Universe ACCEPT: {d.symbol} "
                            f"(score from scanner, expires: {d.expires})"
                        )
                    else:
                        logger.debug(
                            f"✗ Universe REJECT: {d.symbol} ({d.reason})"
                        )
            
            # ========================================================
            # LOAD UNIVERSE
            # ========================================================
            if universe_enabled:
                symbols_to_trade = get_universe_symbols(mode=universe_mode)
            else:
                # Fallback to config-based symbols
                symbols_to_trade = all_symbols  # From strategy config
            
            logger.info(f"Trading {len(symbols_to_trade)} symbols")
            
            # ========================================================
            # STRATEGY EXECUTION (using universe)
            # ========================================================
            for symbol in symbols_to_trade:
                # Fetch bars
                df = broker.get_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=lookback,
                )
                bars = _df_to_contracts(symbol, df)
                
                # Validate bars
                if not data_validator.validate_bars(bars):
                    logger.warning(f"Invalid bars for {symbol}")
                    continue
                
                # Run strategies
                for strategy in lifecycle.get_active_strategies():
                    if symbol in strategy.symbols:
                        signals = strategy.on_data(symbol, bars)
                        
                        # Process signals...
                        # (existing signal processing logic)
            
            # Sleep before next cycle
            time.sleep(opts.run_interval_s)
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(5)
```

===============================================================================
ENVIRONMENT VARIABLES
===============================================================================

Add to .env or phase config:

```bash
# Enable universe system (1=enabled, 0=disabled)
UNIVERSE_ENABLED=1

# Universe mode (hybrid|scanner|accepted)
# hybrid = CORE + accepted (recommended)
# scanner = only accepted symbols (no CORE)
# accepted = CORE + accepted (same as hybrid)
UNIVERSE_MODE=hybrid

# Universe data directory
UNIVERSE_DATA_DIR=data/universe

# Gate 2 check interval (seconds, if using integrated mode)
UNIVERSE_CHECK_INTERVAL=60
```

===============================================================================
TESTING
===============================================================================

1. Apply trading bot patch (Option 1 or 2)

2. Start scanner (Gate 1):
```powershell
python -m scanners.standalone_scanner
```

3. Start trading bot:
```powershell
python entry_paper.py
```

4. Watch logs for:
```
✓ Universe system enabled (mode=hybrid)
Trading 4 symbols  # CORE + accepted
✓ Universe ACCEPT: TSLA (score from scanner)
✗ Universe REJECT: AAPL (spread_filter_failed)
```

5. Check universe file:
```powershell
cat data\universe\universe_active.json
```

Expected:
```json
{
  "core": ["SPY", "QQQ"],
  "accepted": ["TSLA", "NVDA"],
  "expires_by_symbol": {
    "TSLA": "2026-01-26T18:13:00Z",
    "NVDA": "2026-01-26T19:45:00Z"
  },
  ...
}
```

===============================================================================
MINIMAL INTEGRATION (Quick Test)
===============================================================================

If you just want to test universe loading without Gate 2 processing:

```python
# In app.py, replace symbol list:

from core.universe import get_universe_symbols

# BEFORE
all_symbols = ["SPY", "QQQ", "AAPL"]  # Hardcoded

# AFTER
all_symbols = get_universe_symbols(mode="hybrid")  # Dynamic
```

Then manually populate universe:
```powershell
# Add symbols to universe manually
python -c "import json; from pathlib import Path; p = Path('data/universe/universe_active.json'); data = json.loads(p.read_text()); data['accepted'] = ['TSLA', 'NVDA']; p.write_text(json.dumps(data, indent=2))"
```

===============================================================================
TROUBLESHOOTING
===============================================================================

**Issue: Universe stays empty**

Solution: Check scanner is writing to inbox.jsonl
```powershell
dir data\universe\inbox.jsonl
cat data\universe\inbox.jsonl
```

**Issue: All candidates rejected**

Solution: Check Gate 2 filters in core/universe/inbox.py
- Lower MIN_DOLLAR_VOLUME
- Widen spread filter
- Increase MAX_ACCEPTED_PER_DAY

**Issue: Bot not reading universe**

Solution: Verify universe_active.json exists and has data
```powershell
cat data\universe\universe_active.json
```

**Issue: Stale symbols**

Solution: Run purge manually
```powershell
python -c "from core.universe import UniverseInboxProcessor; from core.time import RealTimeClock; from pathlib import Path; p = UniverseInboxProcessor(Path('data/universe'), RealTimeClock()); p.purge_expired_symbols()"
```

===============================================================================
STATUS
===============================================================================

✅ Integration code ready
✅ Two modes supported (integrated, daemon)
✅ Environment configuration documented
✅ Testing procedure defined
✅ Troubleshooting guide included

READY TO APPLY to core/runtime/app.py
"""
