# Two-Gate Universe System - Complete Integration Guide

## 📋 OVERVIEW

File-based "scanner → universe → tradingbot" pipeline with two filtering gates:

```
Scanner (Gate 1) → inbox.jsonl → Trading Bot (Gate 2) → universe_active.json → Trading Engine
```

**Gate 1 (Scanner):** Identifies high-scoring candidates  
**Gate 2 (Trading Bot):** Reevaluates with tighter filters, enforces limits  
**Universe:** CORE (SPY, QQQ) + accepted rolling window (24hr expiry)

---

## 🏗️ ARCHITECTURE

### Files Created

```
MiniQuantDeskv2/
├── data/universe/
│   ├── inbox.jsonl           # Scanner writes candidates (Gate 1 output)
│   ├── decisions.jsonl        # Bot writes accept/reject (Gate 2 output)
│   ├── universe_active.json   # Atomic snapshot (read by trading engine)
│   └── state.json             # Internal state (offset, daily count)
│
├── core/universe/
│   ├── __init__.py            # Public API exports
│   ├── inbox.py               # Gate 2 processor (UniverseInboxProcessor)
│   ├── scanner_adapter.py     # Gate 1 output writer
│   ├── loader.py              # Universe loader for trading bot
│   └── daemon.py              # Background processor (optional)
│
└── scanners/
    └── INTEGRATION_PATCH.md   # Scanner integration guide
```

### Data Flow

```
[Scanner detects TSLA spike]
  ↓
scanner_adapter.write_candidate()
  ↓
data/universe/inbox.jsonl
  ↓
UniverseInboxProcessor.process_new_candidates()
  ↓ (applies filters)
  ↓
data/universe/decisions.jsonl (accept/reject)
  ↓
data/universe/universe_active.json (atomic update)
  ↓
UniverseLoader.get_symbols()
  ↓
[Trading engine uses symbols]
```

---

## 🚀 QUICK START

### Step 1: Scanner Integration (Gate 1)


**In your scanner code (standalone_scanner.py):**

```python
# At top of file
from core.universe import get_scanner_adapter

# In scanner initialization
scanner_adapter = get_scanner_adapter()

# When high-scoring symbol is detected
if score >= 7.0:  # Your threshold
    scanner_adapter.write_candidate(
        symbol="TSLA",
        score=8.5,
        session="rth",  # or "pre" for premarket
        features={
            "rvol": 3.2,
            "gap_pct": 5.1,
            "spread_bps": 8,
            "dollar_vol": 15000000,
            "atr_pct": 2.5,
        },
        levels={
            "hold": 245.00,
            "break": 250.00,
            "t1": 260.00,
            "t2": 275.00,
        },
    )
```

**Result:** Candidate written to `data/universe/inbox.jsonl`

---

### Step 2: Gate 2 Processing (Trading Bot)

**Option A: Manual Processing (in your trading loop)**

```python
from core.universe import UniverseInboxProcessor
from core.time import RealTimeClock

# Initialize processor
clock = RealTimeClock()
data_dir = Path("data/universe")
processor = UniverseInboxProcessor(data_dir, clock)

# In trading loop (every minute)
decisions = processor.process_new_candidates(
    has_open_position=has_position,
    has_open_orders=has_orders,
)

# Log decisions
for d in decisions:
    logger.info(f"{d.symbol}: {d.decision} ({d.reason})")
```

**Option B: Background Daemon (recommended)**

```powershell
# Run in separate terminal
python -m core.universe.daemon
```

Daemon runs every 60 seconds, processes inbox, updates universe.

---

### Step 3: Trading Bot Uses Universe

**In your trading bot (app.py or strategy):**

```python
from core.universe import get_universe_symbols

# Get symbols to trade
symbols = get_universe_symbols(mode="hybrid")  # CORE + accepted

# symbols = ["SPY", "QQQ", "TSLA", "NVDA", ...]

# Use in strategy
for symbol in symbols:
    # Run strategy logic
    pass
```

**Modes:**
- `scanner`: Only accepted symbols (no CORE)
- `accepted`: CORE + accepted symbols
- `hybrid`: Same as accepted (recommended)

---

## ⚙️ CONFIGURATION

### Environment Variables

```bash
# Universe mode
UNIVERSE_MODE=hybrid  # scanner|accepted|hybrid

# Data directory (default: data/universe)
UNIVERSE_DATA_DIR=data/universe

# Daemon check interval (default: 60 seconds)
UNIVERSE_CHECK_INTERVAL=60
```

### Gate 2 Filters (in core/universe/inbox.py)

```python
# Spread filter
MIN_SPREAD_BPS = 5
MAX_SPREAD_BPS = 50

# Volume filter
MIN_DOLLAR_VOLUME = 5_000_000  # $5M daily

# Price range
MIN_PRICE = Decimal("5.00")
MAX_PRICE = Decimal("500.00")

# ATR sanity
MIN_ATR_RATIO = Decimal("0.5")  # 0.5% of price

# Daily limits
MAX_ACCEPTED_PER_DAY = 10

# Expiry
SYMBOL_EXPIRY_HOURS = 24  # 24 hours from acceptance
```

---

## 📊 GLOBAL RULES (Gate 2)

1. **One trade at a time:**  
   If bot has open position or orders → reject all candidates

2. **Premarket = watch only:**  
   Candidates can be accepted during premarket  
   Trading (order submission) waits until RTH

3. **Max accepted per day:**  
   Default: 10 symbols/day  
   Prevents runaway universe growth

4. **Symbol expiry:**  
   Symbols expire 24 hours from acceptance  
   Purged automatically on next universe rebuild

---

## 📁 FILE FORMATS

### inbox.jsonl (Scanner Output)

```json
{
  "id": "2026-01-25T18:12:00Z:TSLA:scanner_v2",
  "ts": "2026-01-25T18:12:00Z",
  "symbol": "TSLA",
  "session": "rth",
  "score": 8.5,
  "features": {
    "rvol": 3.2,
    "gap_pct": 5.1,
    "spread_bps": 8,
    "dollar_vol": 15000000,
    "atr_pct": 2.5
  },
  "levels": {
    "hold": 245.00,
    "break": 250.00,
    "t1": 260.00,
    "t2": 275.00
  },
  "source": "scanner_v2",
  "version": "2.1"
}
```

### decisions.jsonl (Gate 2 Output)

```json
{
  "id": "2026-01-25T18:12:00Z:TSLA:scanner_v2",
  "ts": "2026-01-25T18:13:00Z",
  "symbol": "TSLA",
  "decision": "accept",
  "expires": "2026-01-26T18:13:00Z",
  "reason": "passed_all_filters_score_8.5",
  "bot_version": "v1"
}
```

### universe_active.json (Atomic Snapshot)

```json
{
  "core": ["SPY", "QQQ"],
  "accepted": ["TSLA", "NVDA"],
  "expires_by_symbol": {
    "TSLA": "2026-01-26T18:13:00Z",
    "NVDA": "2026-01-26T19:45:00Z"
  },
  "last_updated": "2026-01-25T18:13:00Z",
  "version": "1.0"
}
```

### state.json (Internal State)

```json
{
  "inbox_offset": 42,
  "last_processed_id": "2026-01-25T18:12:00Z:TSLA:scanner_v2",
  "daily_accept_count": 5,
  "daily_accept_date": "2026-01-25",
  "version": "1.0"
}
```

---

## 🧪 TESTING

### Test Scanner Output

```python
from core.universe import get_scanner_adapter

adapter = get_scanner_adapter()

# Write test candidate
adapter.write_candidate(
    symbol="TEST",
    score=9.0,
    features={"dollar_vol": 10000000},
    levels={"hold": 100.0},
)

# Check file
# cat data/universe/inbox.jsonl
```

### Test Gate 2 Processing

```python
from pathlib import Path
from core.universe import UniverseInboxProcessor
from core.time import RealTimeClock

processor = UniverseInboxProcessor(
    data_dir=Path("data/universe"),
    clock=RealTimeClock(),
)

decisions = processor.process_new_candidates()
print(f"Processed: {len(decisions)} candidates")
```

### Test Universe Loading

```python
from core.universe import get_universe_symbols

symbols = get_universe_symbols(mode="hybrid")
print(f"Universe: {symbols}")
# Expected: ['SPY', 'QQQ', 'TEST']
```

---

## 🔧 COMMON WORKFLOWS

### Workflow 1: Full Automated Pipeline

```powershell
# Terminal 1: Run scanner (Gate 1)
python -m scanners.standalone_scanner

# Terminal 2: Run Gate 2 processor
python -m core.universe.daemon

# Terminal 3: Run trading bot
python entry_paper.py
```

Scanner writes candidates → Daemon processes → Bot trades universe

### Workflow 2: Manual Processing

```python
# In your trading bot main loop
from core.universe import UniverseInboxProcessor, get_universe_symbols

processor = UniverseInboxProcessor(...)

while running:
    # Process new candidates
    decisions = processor.process_new_candidates(
        has_open_position=check_positions(),
        has_open_orders=check_orders(),
    )
    
    # Load universe
    symbols = get_universe_symbols()
    
    # Trade symbols
    for symbol in symbols:
        run_strategy(symbol)
    
    time.sleep(60)
```

---

## 📌 IMPORTANT NOTES

### Deduplication

- Scanner adapter deduplicates within 5 minutes
- Same symbol won't appear twice unless 5+ minutes elapsed
- Prevents inbox spam from scanner

### Atomic Updates

- universe_active.json is updated atomically (temp file + rename)
- Trading bot always reads consistent snapshot
- No partial updates or race conditions

### Expiry

- Symbols expire 24 hours from acceptance
- Purged automatically when universe rebuilds
- Prevents stale symbols from lingering

### Position Safety

- If bot has open position → reject all new symbols
- One trade at a time rule
- Prevents overtrading

### Premarket Handling

- Scanner can emit premarket candidates (session="pre")
- Gate 2 accepts them for universe
- Trading bot waits until RTH to submit orders
- No actual trading during premarket

---

## 🚨 TROUBLESHOOTING

### Inbox not being processed

```powershell
# Check state.json
cat data\universe\state.json

# Check inbox file exists
dir data\universe\inbox.jsonl

# Manually trigger processing
python -c "from core.universe import UniverseInboxProcessor; from pathlib import Path; from core.time import RealTimeClock; p = UniverseInboxProcessor(Path('data/universe'), RealTimeClock()); p.process_new_candidates()"
```

### Universe empty

```powershell
# Check universe file
cat data\universe\universe_active.json

# Check decisions
cat data\universe\decisions.jsonl

# Force rebuild
python -c "from core.universe import UniverseInboxProcessor; from pathlib import Path; from core.time import RealTimeClock; p = UniverseInboxProcessor(Path('data/universe'), RealTimeClock()); p.purge_expired_symbols()"
```

### All candidates rejected

Check Gate 2 filters in `core/universe/inbox.py`:
- Spread filter
- Dollar volume filter
- Price range filter
- Daily limit (10/day default)

---

## 📚 API REFERENCE

### ScannerOutputAdapter (Gate 1)

```python
from core.universe import get_scanner_adapter

adapter = get_scanner_adapter()
adapter.write_candidate(
    symbol="TSLA",
    score=8.5,
    session="rth",  # "pre" or "rth"
    features={...},
    levels={...},
)
```

### UniverseInboxProcessor (Gate 2)

```python
from core.universe import UniverseInboxProcessor
from core.time import RealTimeClock
from pathlib import Path

processor = UniverseInboxProcessor(
    data_dir=Path("data/universe"),
    clock=RealTimeClock(),
)

decisions = processor.process_new_candidates(
    has_open_position=False,
    has_open_orders=False,
)

symbols = processor.get_active_universe()
```

### UniverseLoader (Trading Bot)

```python
from core.universe import UniverseLoader, get_universe_symbols

# Method 1: Loader object
loader = UniverseLoader(mode="hybrid")
symbols = loader.get_symbols()

# Method 2: Convenience function
symbols = get_universe_symbols(mode="hybrid")
```

---

## ✅ STATUS

**IMPLEMENTATION COMPLETE**

- ✅ Directory structure created
- ✅ Gate 2 processor implemented
- ✅ Scanner adapter created
- ✅ Universe loader implemented
- ✅ Background daemon created
- ✅ Integration guide written
- ✅ File formats documented
- ✅ Testing examples provided

**READY FOR:**
- Scanner integration (add write_candidate() calls)
- Trading bot integration (use UniverseLoader)
- Production testing

---

## 🎯 NEXT STEPS

1. **Integrate scanner:**  
   Add `scanner_adapter.write_candidate()` calls to standalone_scanner.py

2. **Test Gate 2:**  
   Run daemon and verify decisions.jsonl

3. **Integrate trading bot:**  
   Replace hardcoded symbol list with `get_universe_symbols()`

4. **Monitor:**  
   Watch logs for accept/reject decisions

5. **Tune filters:**  
   Adjust Gate 2 filters in inbox.py as needed

---

**Questions?** Check:
- `scanners/INTEGRATION_PATCH.md` for scanner integration
- `core/universe/` source code for implementation details
- This guide for workflows and API reference
