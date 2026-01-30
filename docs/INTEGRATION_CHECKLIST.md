# TWO-GATE UNIVERSE SYSTEM - INTEGRATION CHECKLIST

Use this checklist to track your integration progress.

---

## ✅ PHASE 1: SYSTEM SETUP (Complete)

- [x] Core universe system created (`core/universe/`)
- [x] Data infrastructure initialized (`data/universe/`)
- [x] Documentation written
- [x] Test script created
- [x] Integration patches prepared

**Status:** ✅ COMPLETE - System is production-ready

---

## 🔲 PHASE 2: VERIFICATION (Your Task)

### Step 1: Run End-to-End Test

```powershell
python test_universe_system.py
```

- [ ] Test runs without errors
- [ ] inbox.jsonl created with test candidates
- [ ] decisions.jsonl shows accept/reject
- [ ] universe_active.json contains accepted symbols
- [ ] Universe loader returns symbol list

**Expected output:**
```
✓ Test 1 complete: Check data/universe/inbox.jsonl
✓ Test 2 complete: Check data/universe/decisions.jsonl
✓ Test 3 complete
✓ Test 4 complete
✓ Test 5 complete
✓ ALL TESTS COMPLETE
```

### Step 2: Verify Files

```powershell
dir data\universe
cat data\universe\inbox.jsonl
cat data\universe\decisions.jsonl
cat data\universe\universe_active.json
```

- [ ] All 4 files exist
- [ ] inbox.jsonl has valid JSON
- [ ] decisions.jsonl has valid JSON
- [ ] universe_active.json has CORE + accepted

### Step 3: Test Universe Loader

```powershell
python -c "from core.universe import get_universe_symbols; print(get_universe_symbols())"
```

- [ ] Command works without errors
- [ ] Returns symbol list
- [ ] Includes SPY and QQQ (CORE)

**Phase 2 Target:** All tests pass ✅

---

## 🔲 PHASE 3: SCANNER INTEGRATION (Your Task)

### Step 1: Review Integration Patch

```powershell
notepad scanners\SCANNER_INTEGRATION_PATCH.py
```

- [ ] Read complete patch file
- [ ] Understand integration points
- [ ] Identify where to add code in standalone_scanner.py

### Step 2: Add Imports

In `scanners/standalone_scanner.py`, at top:

```python
# Universe integration (Gate 1)
try:
    from core.universe import get_scanner_adapter
    UNIVERSE_ENABLED = True
except ImportError:
    UNIVERSE_ENABLED = False
```

- [ ] Imports added
- [ ] No import errors when running scanner

### Step 3: Initialize Adapter

In `ScannerEngine.__init__()`:

```python
# Universe integration (Gate 1)
self.universe_adapter = None
if UNIVERSE_ENABLED:
    try:
        self.universe_adapter = get_scanner_adapter()
        print("✓ Universe inbox writer enabled")
    except Exception as e:
        print(f"WARNING: Failed to init universe adapter: {e}")
```

- [ ] Adapter initialization added
- [ ] Scanner starts with "✓ Universe inbox writer enabled"

### Step 4: Write Candidates

Find where candidates are scored, add:

```python
# Write high-scoring candidates to universe inbox (Gate 1)
if self.universe_adapter and candidate.score >= 7.0:
    self._write_to_universe_inbox(candidate, session)
```

- [ ] Call added after candidate creation
- [ ] Method `_write_to_universe_inbox()` implemented

### Step 5: Test Scanner Integration

```powershell
python -m scanners.standalone_scanner
```

- [ ] Scanner starts without errors
- [ ] When high-scoring symbol found → see "→ Universe: SYMBOL"
- [ ] Check inbox.jsonl grows: `cat data\universe\inbox.jsonl`

**Phase 3 Target:** Scanner writes to inbox.jsonl ✅

---

## 🔲 PHASE 4: TRADING BOT INTEGRATION (Your Task)

### Option A: Integrated Mode (Recommended)

#### Step 1: Review Integration Patch

```powershell
notepad TRADING_BOT_INTEGRATION_PATCH.py
```

- [ ] Read complete patch file
- [ ] Understand integration approach
- [ ] Choose integrated or daemon mode

#### Step 2: Add Universe System to app.py

In `core/runtime/app.py`, after broker initialization:

```python
from pathlib import Path as UnivPath
from core.universe import UniverseInboxProcessor, get_universe_symbols

universe_processor = UniverseInboxProcessor(
    data_dir=UnivPath("data/universe"),
    clock=container.get_clock(),
)
```

- [ ] Imports added
- [ ] Processor initialized
- [ ] No errors on startup

#### Step 3: Add Gate 2 Processing to Main Loop

```python
# In main loop, before strategy execution:
decisions = universe_processor.process_new_candidates(
    has_open_position=len(positions) > 0,
    has_open_orders=len(orders) > 0,
)

symbols_to_trade = get_universe_symbols(mode="hybrid")
```

- [ ] Processing added to main loop
- [ ] Universe loading added
- [ ] Symbols dynamically loaded each cycle

#### Step 4: Replace Hardcoded Symbols

BEFORE:
```python
for symbol in all_symbols:  # Hardcoded
```

AFTER:
```python
for symbol in symbols_to_trade:  # Dynamic universe
```

- [ ] Symbol loop updated
- [ ] Bot uses dynamic universe

#### Step 5: Test Trading Bot Integration

```powershell
python entry_paper.py
```

- [ ] Bot starts without errors
- [ ] See "✓ Universe system enabled"
- [ ] Logs show: "Trading N symbols"
- [ ] decisions.jsonl grows when processing candidates

**Phase 4A Target:** Bot processes candidates and uses universe ✅

### Option B: Daemon Mode (Alternative)

#### Step 1: Run Daemon in Separate Terminal

```powershell
# Terminal 1
python -m core.universe.daemon
```

- [ ] Daemon starts without errors
- [ ] Logs show "Starting universe daemon"
- [ ] Processes candidates every 60 seconds

#### Step 2: Simplify Trading Bot

In `app.py`, just load universe:

```python
from core.universe import get_universe_symbols

symbols_to_trade = get_universe_symbols(mode="hybrid")
```

- [ ] Universe loading added
- [ ] Bot uses dynamic universe
- [ ] No Gate 2 processing in bot (daemon handles it)

#### Step 3: Test Daemon Mode

```powershell
# Terminal 1
python -m core.universe.daemon

# Terminal 2  
python entry_paper.py
```

- [ ] Daemon processes candidates
- [ ] Bot loads universe correctly
- [ ] Both run independently

**Phase 4B Target:** Daemon + Bot working together ✅

---

## 🔲 PHASE 5: FULL SYSTEM TEST (Your Task)

### Step 1: Run Complete Pipeline

```powershell
# Terminal 1: Scanner (Gate 1)
python -m scanners.standalone_scanner

# Terminal 2: Daemon (Gate 2) - if using daemon mode
python -m core.universe.daemon

# Terminal 3: Trading Bot
python entry_paper.py
```

- [ ] All components start cleanly
- [ ] Scanner finds candidates
- [ ] Gate 2 processes candidates  
- [ ] Trading bot uses universe

### Step 2: Verify Data Flow

```powershell
# Watch inbox grow
Get-Content data\universe\inbox.jsonl -Tail 5 -Wait

# Watch decisions
Get-Content data\universe\decisions.jsonl -Tail 5 -Wait

# Check universe
cat data\universe\universe_active.json
```

- [ ] Inbox receives candidates from scanner
- [ ] Decisions show accept/reject
- [ ] Universe updates with accepted symbols

### Step 3: Monitor Logs

Watch for these log messages:

```
✓ Universe inbox writer enabled          # Scanner
✓ Universe ACCEPT: TSLA                   # Gate 2
✗ Universe REJECT: AAPL (spread_filter)  # Gate 2
Trading 5 symbols                         # Bot
```

- [ ] Scanner logs universe writes
- [ ] Gate 2 logs decisions
- [ ] Bot logs symbol count

### Step 4: Test Edge Cases

- [ ] Scanner finds low-score symbol → Not written to inbox
- [ ] Gate 2 gets high-spread symbol → Rejected
- [ ] Bot has position → New candidates rejected (one trade rule)
- [ ] Symbol expires after 24hr → Removed from universe
- [ ] Daily limit reached (10) → New candidates rejected

**Phase 5 Target:** Full pipeline working end-to-end ✅

---

## 🔲 PHASE 6: PRODUCTION DEPLOYMENT (Your Task)

### Configuration

Add to `.env` or phase config:

```bash
UNIVERSE_ENABLED=1
UNIVERSE_MODE=hybrid
UNIVERSE_DATA_DIR=data/universe
UNIVERSE_CHECK_INTERVAL=60
```

- [ ] Environment variables set
- [ ] Configuration tested

### Monitoring

Set up monitoring for:

```powershell
# File sizes
dir data\universe

# Recent decisions
Get-Content data\universe\decisions.jsonl | Select-Object -Last 10

# Current universe
cat data\universe\universe_active.json | python -m json.tool
```

- [ ] Monitoring commands work
- [ ] Can inspect system state

### Tuning

Adjust filters in `core/universe/inbox.py` if needed:

```python
MIN_SPREAD_BPS = 5         # Tighten or loosen
MAX_SPREAD_BPS = 50
MIN_DOLLAR_VOLUME = 5_000_000
MAX_ACCEPTED_PER_DAY = 10  # Increase if too restrictive
```

- [ ] Filters reviewed
- [ ] Adjusted based on accept/reject ratios

### Backup & Recovery

```powershell
# Backup universe data
Copy-Item -Recurse data\universe data\universe.backup

# Reset if needed
Remove-Item data\universe\*.jsonl
python test_universe_system.py
```

- [ ] Backup procedure documented
- [ ] Recovery tested

**Phase 6 Target:** System in production ✅

---

## 📊 SUCCESS METRICS

After full integration, you should see:

✅ **Scanner Metrics:**
- High-scoring symbols written to inbox
- "→ Universe: SYMBOL" messages in logs
- inbox.jsonl growing steadily

✅ **Gate 2 Metrics:**
- Accept rate: 20-50% (adjust filters if too low/high)
- Daily accepts: < 10 (respecting limit)
- Reject reasons logged clearly

✅ **Trading Bot Metrics:**
- Dynamic symbol list (not hardcoded)
- Universe size: 2-12 symbols (CORE + accepted)
- Position safety enforced

✅ **System Metrics:**
- Files updating atomically
- No corruption or partial writes
- Symbols expiring after 24hr
- Clean logs, no errors

---

## 🚨 TROUBLESHOOTING QUICK REFERENCE

| Issue | Solution |
|-------|----------|
| Test fails | Check Python path, verify imports |
| Universe empty | Run test script, check decisions.jsonl |
| All rejected | Lower filters in inbox.py |
| Scanner not writing | Verify adapter initialization |
| Bot not loading | Check universe_active.json exists |
| Stale symbols | Run purge_expired_symbols() |

**Full troubleshooting:** See `TWO_GATE_UNIVERSE_GUIDE.md`

---

## 📞 HELP RESOURCES

- **Complete Guide:** `TWO_GATE_UNIVERSE_GUIDE.md`
- **Architecture:** `ARCHITECTURE_DIAGRAM.md`
- **Scanner Integration:** `scanners/SCANNER_INTEGRATION_PATCH.py`
- **Bot Integration:** `TRADING_BOT_INTEGRATION_PATCH.py`
- **Testing:** `test_universe_system.py`

---

## 🎉 COMPLETION CHECKLIST

- [ ] Phase 1: System setup ✅ (already complete)
- [ ] Phase 2: Verification (test script passes)
- [ ] Phase 3: Scanner integration (writes to inbox)
- [ ] Phase 4: Trading bot integration (processes & loads)
- [ ] Phase 5: Full system test (end-to-end working)
- [ ] Phase 6: Production deployment (monitoring & tuning)

**When all phases complete: SYSTEM FULLY OPERATIONAL** 🚀

---

*Last Updated: 2026-01-25*  
*System Version: 1.0*  
*Status: Ready for Integration*
