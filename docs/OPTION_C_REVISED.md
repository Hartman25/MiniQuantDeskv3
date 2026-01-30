# OPTION C - REVISED ACTIVATION PLAN

**Date:** January 24, 2026  
**Revision Reason:** Throttler + UserStreamTracker require async refactor  
**New Approach:** Activate sync features NOW, async features in Phase 2

---

## DISCOVERY: ASYNC BARRIER

**Problem Found:**
- Throttler uses `async/await` (requires asyncio)
- UserStreamTracker uses `async/await` (WebSocket is async)
- Current broker connector is **synchronous**
- Refactoring to async = major change, affects all callers

**Decision:**
Instead of blocking on async refactor, activate the **3 synchronous features** today:
1. ✅ Clock Abstraction (ALREADY ACTIVE)
2. ✅ Protections (sync pre-trade checks)
3. ✅ OrderTracker (sync order tracking)
4. ✅ SymbolProperties (sync validation/rounding)

**Defer to Phase 2:**
5. ⏳ Throttler (needs async)
6. ⏳ UserStreamTracker (needs async)

---

## REVISED ACTIVATION SEQUENCE (TODAY)

### STEP 1: Activate Protections (30 min) ⏳
**File:** `core/risk/gate.py`  
**Risk:** LOW  
**Benefit:** Prevents trading after losses/drawdowns

Add protection checks before risk gate evaluation.

---

### STEP 2: Activate Symbol Properties (30 min) ⏳
**Files:** Wherever orders are created  
**Risk:** LOW  
**Benefit:** Prevents invalid orders (wrong lot sizes, illegal prices)

Add validation and rounding before order submission.

---

### STEP 3: Activate OrderTracker (1 hour) ⏳
**Files:** Execution engine, broker connector  
**Risk:** LOW  
**Benefit:** Detects orphan orders, tracks fills

Wire OrderTracker into order lifecycle.

---

### STEP 4: Test Everything (30 min) ⏳
Run paper trading session, verify:
- Protections trigger correctly
- Symbol properties round values
- OrderTracker tracks orders
- No errors

---

## PHASE 2 ADDITIONS (ASYNC REFACTOR)

Once we refactor to async (Phase 2 priority):

### Add Throttler
- Convert broker connector to async
- Wrap all API calls with throttler
- Benefit: Zero rate limit violations

### Add UserStreamTracker
- Start WebSocket in async context
- Real-time fills < 100ms
- Benefit: Faster execution, better fill tracking

---

## WHY THIS MAKES SENSE

**For Option C (Activate + Test + Iterate):**
1. Get 4/6 features active TODAY
2. Start collecting real-world data
3. Build Phase 2 while system runs
4. Add async when we refactor (cleaner, not rushed)

**Better than:**
- Rushing async refactor (error-prone)
- Delaying all features (no progress)
- Partial async (messy hybrid)

---

## WHAT YOU GET TODAY

**Active Features (4/6):**
- ✅ Clock - Backtest-safe time
- ✅ Protections - Circuit breakers
- ✅ OrderTracker - Fill tracking
- ✅ SymbolProperties - Order validation

**Deferred (2/6):**
- ⏳ Throttler - Rate limiting (Phase 2)
- ⏳ UserStreamTracker - WebSocket fills (Phase 2)

**Still valuable:**
- 4 major features active
- System more robust
- Ready for paper trading
- Async refactor done properly in Phase 2

---

## TIME ESTIMATE

**Today (Revised):**
- Protections: 30 min
- SymbolProperties: 30 min
- OrderTracker: 1 hour
- Testing: 30 min
**Total: 2.5 hours** (vs 3-4 hours original)

**Phase 2:**
- Async refactor: 1-2 days
- Throttler integration: 2 hours
- UserStreamTracker integration: 2 hours

---

## PROCEED?

**Option A:** Activate 3 sync features now (2.5 hours)
**Option B:** Wait and do full async refactor first (2-3 days)
**Option C:** Just test current config without activating anything

Which do you prefer?

My recommendation: **Option A** (activate sync features, async in Phase 2)
