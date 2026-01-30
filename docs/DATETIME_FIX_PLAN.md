# DATETIME.UTCNOW() REPLACEMENT PLAN

**Status:** Starting systematic replacement  
**Total Instances:** 44  
**Priority:** CRITICAL (blocks backtesting)

---

## STRATEGY

### Problem with Dataclass default_factory
```python
# OLD (Line 58 in order.py):
created_at: datetime = field(default_factory=datetime.utcnow)

# CAN'T DO THIS (no clock access in dataclass):
created_at: datetime = field(default_factory=lambda: self.clock.now())
```

### Solution: Remove defaults, require explicit time
```python
# NEW:
created_at: datetime  # Required, no default

# Creator must provide:
order = Order(
    order_id="123",
    symbol="SPY",
    created_at=self.clock.now(),  # Explicit
    ...
)
```

---

## FILES TO FIX (Priority Order)

### TIER 1: Core State (CRITICAL)
1. `core/state/order.py` - Line 58
   - Remove default_factory
   - Update all Order() creators
   
2. `core/state/position_store.py` - Line 235
   - Inject clock
   - Use clock.now()
   
3. `core/state/transaction_log.py` - Line 113
   - Inject clock
   - Use clock.now()

### TIER 2: Portfolio (HIGH)
4. `core/portfolio/manager.py` - Line 153
   - Inject clock
   - Use clock.now() in order ID generation

### TIER 3: Strategies (MEDIUM)
5. `core/strategies/vwap_mean_reversion.py` - Lines 52, 66
   - Inject clock
   - Use clock.now()

### TIER 4: Logging (LOW - can defer)
6. `core/logging/config.py` - Lines 46, 74
7. `core/logging/formatters.py` - Multiple lines

**Logging Note:** These are for log timestamps, not critical for trading logic. Can use real time even in backtesting. DEFER to later.

---

## EXECUTION PLAN

### Step 1: Fix Order dataclass ✅
- Remove default_factory from created_at
- Find all `Order(...)` calls
- Update to require created_at

### Step 2: Fix PositionStore ✅
- Add clock parameter to __init__
- Replace datetime.utcnow() → self.clock.now()

### Step 3: Fix TransactionLog ✅
- Add clock parameter to __init__
- Replace datetime.utcnow() → self.clock.now()

### Step 4: Update Container ✅
- Pass clock when creating PositionStore
- Pass clock when creating TransactionLog

### Step 5: Test ✅
- Verify no import errors
- Verify container initialization
- Verify Order creation works

---

**Starting now...**
