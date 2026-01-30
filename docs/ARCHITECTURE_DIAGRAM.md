# TWO-GATE UNIVERSE SYSTEM - ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TWO-GATE UNIVERSE SYSTEM ARCHITECTURE                     │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ GATE 1: SCANNER (Candidate Identification)                                  │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────────────────┐
    │  Market Scanner      │
    │  (standalone_        │
    │   scanner.py)        │
    └──────────┬───────────┘
               │ Identifies high-scoring symbols
               │ Score >= 7.0
               │
               ▼
    ┌──────────────────────┐
    │ ScannerOutput        │
    │ Adapter              │
    │ (Gate 1 Writer)      │
    └──────────┬───────────┘
               │ Writes candidate
               │ Deduplicates (5min window)
               │
               ▼
    ┌──────────────────────────────────────┐
    │  data/universe/inbox.jsonl           │
    │  ─────────────────────────────────   │
    │  {"symbol": "TSLA", "score": 8.5}   │
    │  {"symbol": "NVDA", "score": 7.8}   │
    │  {"symbol": "AAPL", "score": 6.2}   │
    └──────────────────┬───────────────────┘
                       │
                       │
┌──────────────────────┴───────────────────────────────────────────────────────┐
│ GATE 2: TRADING BOT (Reevaluation & Filtering)                              │
└──────────────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
    ┌─────────────────────────────────┐
    │  UniverseInboxProcessor         │
    │  (Gate 2 Processor)             │
    │  ───────────────────────────    │
    │  1. Read new lines from inbox   │
    │  2. Apply filters:              │
    │     • Spread (5-50 bps)         │
    │     • Dollar volume ($5M min)   │
    │     • Price range ($5-$500)     │
    │     • ATR sanity (0.5% min)     │
    │  3. Check global rules:         │
    │     • One trade at a time       │
    │     • Max 10 accepted/day       │
    │     • Premarket = watch only    │
    │  4. Write decision              │
    └────────┬──────────────┬─────────┘
             │              │
             │ ACCEPT       │ REJECT
             │              │
             ▼              ▼
    ┌──────────────┐  ┌──────────────┐
    │ TSLA: ACCEPT │  │ AAPL: REJECT │
    │ (expires     │  │ (spread_     │
    │  24 hours)   │  │  filter)     │
    └──────┬───────┘  └──────┬───────┘
           │                 │
           ▼                 ▼
    ┌──────────────────────────────────────┐
    │  data/universe/decisions.jsonl       │
    │  ─────────────────────────────────   │
    │  {"symbol": "TSLA", "decision":      │
    │   "accept", "expires": "..."}        │
    │  {"symbol": "NVDA", "decision":      │
    │   "accept", "expires": "..."}        │
    │  {"symbol": "AAPL", "decision":      │
    │   "reject", "reason": "spread"}      │
    └──────────────────┬───────────────────┘
                       │
                       │ Atomic rebuild
                       │
                       ▼
    ┌──────────────────────────────────────┐
    │  data/universe/universe_active.json  │
    │  ─────────────────────────────────   │
    │  {                                   │
    │    "core": ["SPY", "QQQ"],          │
    │    "accepted": ["TSLA", "NVDA"],    │
    │    "expires_by_symbol": {           │
    │      "TSLA": "2026-01-26T18:13Z",  │
    │      "NVDA": "2026-01-26T19:45Z"   │
    │    }                                 │
    │  }                                   │
    └──────────────────┬───────────────────┘
                       │
                       │
┌──────────────────────┴───────────────────────────────────────────────────────┐
│ TRADING ENGINE (Execution)                                                   │
└──────────────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
    ┌─────────────────────────────────┐
    │  UniverseLoader                 │
    │  (get_universe_symbols)         │
    │  ───────────────────────────    │
    │  Loads: CORE + accepted         │
    │  Mode: hybrid (recommended)     │
    └────────┬────────────────────────┘
             │
             ▼
    ┌──────────────────────────────┐
    │  Trading Bot Main Loop       │
    │  (app.py)                    │
    │  ────────────────────────    │
    │  symbols = ["SPY", "QQQ",    │
    │             "TSLA", "NVDA"]  │
    │                              │
    │  for symbol in symbols:      │
    │    run_strategy(symbol)      │
    └──────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│ TIMELINE VIEW (Typical Flow)                                                │
└─────────────────────────────────────────────────────────────────────────────┘

09:30 ET  Scanner detects TSLA spike (score 8.5)
          → Writes to inbox.jsonl
          
09:31 ET  Trading bot Gate 2 processor runs
          → Reads TSLA from inbox
          → Checks filters: spread ✓, volume ✓, price ✓
          → Checks rules: no position ✓, under daily limit ✓
          → Decision: ACCEPT
          → Writes to decisions.jsonl
          → Updates universe_active.json (atomic)
          
09:32 ET  Trading bot loads universe
          → Reads universe_active.json
          → Gets: ["SPY", "QQQ", "TSLA"]
          → Runs VWAP MR strategy on all 3 symbols
          
10:00 ET  TSLA triggers entry signal
          → Bot submits order
          → Position opened
          
10:05 ET  Scanner finds NVDA (score 7.8)
          → Writes to inbox.jsonl
          
10:06 ET  Gate 2 processor runs
          → Has open position on TSLA
          → Rule: one trade at a time
          → Decision: REJECT (bot_busy_one_trade_at_a_time)
          
10:30 ET  TSLA position closes
          → Universe still has TSLA (until expiry)
          
Next Day  Expiry check runs
+09:30 ET → TSLA expired (24 hours passed)
          → Removed from universe
          → Back to: ["SPY", "QQQ"]


┌─────────────────────────────────────────────────────────────────────────────┐
│ FILE LIFECYCLE                                                               │
└─────────────────────────────────────────────────────────────────────────────┘

inbox.jsonl
  • Append-only (scanner writes, never deletes)
  • State tracks last processed offset
  • Grows indefinitely (rotate manually if needed)
  • Format: One JSON object per line

decisions.jsonl
  • Append-only (Gate 2 writes, never deletes)  
  • Contains full history (accept + reject)
  • Used to rebuild universe on startup
  • Format: One JSON object per line

universe_active.json
  • Atomic updates (temp file + rename)
  • Always consistent (never partially written)
  • Read by trading engine
  • Rebuilt from decisions.jsonl on each update

state.json
  • Tracks inbox processing position
  • Daily accept count
  • Last processed candidate ID
  • Reset daily for accept count


┌─────────────────────────────────────────────────────────────────────────────┐
│ FAILURE MODES & RECOVERY                                                     │
└─────────────────────────────────────────────────────────────────────────────┘

Scanner crashes
  ↓
  └─→ inbox.jsonl stops growing
      Universe stays static (last good state)
      Trading continues on CORE + last accepted symbols
      
Gate 2 processor crashes
  ↓
  └─→ inbox.jsonl keeps growing (scanner OK)
      Universe stays static
      When processor restarts: catches up from last offset
      
Trading bot crashes
  ↓
  └─→ Scanner and Gate 2 keep running
      Universe keeps updating
      When bot restarts: loads current universe
      
Corrupt universe_active.json
  ↓
  └─→ Fallback to CORE universe ["SPY", "QQQ"]
      Log error
      Continue trading safely
      
All systems down
  ↓
  └─→ On restart:
      • Scanner resumes finding candidates
      • Gate 2 processes from last offset
      • Universe rebuilds from decisions.jsonl
      • Trading resumes on current universe


┌─────────────────────────────────────────────────────────────────────────────┐
│ DEPLOYMENT MODES                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

MODE 1: Integrated (Recommended)
  ┌─────────────────┐
  │  Trading Bot    │
  │  ─────────────  │
  │  • Runs Gate 2  │
  │  • Loads univ.  │
  │  • Executes     │
  └─────────────────┘
  
  Pros: Single process, simpler
  Cons: Gate 2 tied to bot cycle

MODE 2: Daemon (Production)
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Scanner  │  │  Daemon  │  │  Bot     │
  │ (Gate 1) │  │ (Gate 2) │  │ (Loads)  │
  └──────────┘  └──────────┘  └──────────┘
  
  Pros: Independent processes
  Cons: More terminals/services


┌─────────────────────────────────────────────────────────────────────────────┐
│ KEY DESIGN DECISIONS                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

✓ File-based (not DB)
  → Simple, no external dependencies
  → Easy to inspect/debug
  → Atomic operations via temp+rename

✓ JSONL (not JSON array)
  → Append-only, no file rewrites
  → Easy offset-based processing
  → Handles large files efficiently

✓ Two gates (not one)
  → Scanner: fast, permissive (catch everything)
  → Bot: slow, strict (trade only best)
  → Separation of concerns

✓ Atomic updates
  → No partial writes
  → No race conditions
  → Always consistent state

✓ 24hr expiry
  → Symbols don't linger forever
  → Universe stays fresh
  → Auto-cleanup

✓ Daily limits
  → Prevents runaway growth
  → Forces quality over quantity
  → Manageable universe size

✓ Position safety
  → One trade at a time
  → No overtrading
  → Clear risk management
