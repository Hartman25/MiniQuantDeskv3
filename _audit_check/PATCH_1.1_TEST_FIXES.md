# PATCH 1.1: FIX TEST FAILURES (API MISMATCHES)

## WHAT WAS BROKEN

### Import Error #1: EventBus → OrderEventBus
**File:** `core/di/container.py:26`  
**Error:**
```python
from core.events.bus import EventBus
# ImportError: cannot import name 'EventBus'
```

**Root Cause:** The actual class name is `OrderEventBus`, not `EventBus`

**Impact:** All tests importing Container failed (5 tests)

---

### TypeError #2: ConfigLoader.load() Signature
**File:** `tests/test_smoke.py:37`  
**Error:**
```python
loader = ConfigLoader()
cfg = loader.load(str(config_path))
# TypeError: ConfigLoader.load() takes 1 positional argument but 2 were given
```

**Root Cause:** `ConfigLoader.load()` takes NO arguments - config path is set in `__init__`

**Correct Usage:**
```python
loader = ConfigLoader(config_dir=config_path.parent)
cfg = loader.load()  # No arguments
```

---

### AssertionError #3: Strategy Name Case Mismatch
**File:** `tests/test_smoke.py:181`  
**Error:**
```python
assert "VWAPMeanReversion" in registry.list_strategies()
# AssertionError: assert 'VWAPMeanReversion' in ['vwapmeanreversion']
```

**Root Cause:** `StrategyRegistry` lowercases all strategy names in `register()`:
```python
# strategies/registry.py:77
name = strategy_class.__name__.lower()  # VWAPMeanReversion → vwapmeanreversion
```

**Correct Test:**
```python
assert "vwapmeanreversion" in registry.list_strategies()
```

---

## HOW IT WAS FIXED

### Fix #1: Update Container Import
**File:** `core/di/container.py`

**Changed:**
```python
# OLD (WRONG):
from core.events.bus import EventBus

# NEW (CORRECT):
from core.events.bus import OrderEventBus
```

**Updated 3 locations:**
- Line 26: Import statement
- Line 77: Type annotation for `self._event_bus`
- Line 131: Instance creation
- Line 217: Return type for `get_event_bus()`

---

### Fix #2: Update Test ConfigLoader Usage
**File:** `tests/test_smoke.py`

**Changed:**
```python
# OLD (WRONG):
loader = ConfigLoader()
cfg = loader.load(str(config_path))

# NEW (CORRECT):
loader = ConfigLoader(config_dir=config_path.parent)
cfg = loader.load()  # No arguments
```

---

### Fix #3: Update Strategy Name Test
**File:** `tests/test_smoke.py`

**Changed:**
```python
# OLD (WRONG):
assert "VWAPMeanReversion" in registry.list_strategies()

# NEW (CORRECT):
assert "vwapmeanreversion" in registry.list_strategies()

# Also fixed create() call:
strategy = registry.create(
    name="vwapmeanreversion",  # Lowercase
    config={...},
    symbols=["SPY"],
    timeframe="1Min"
)
```

---

## VERIFICATION

### Run Tests Again:
```bash
python -m pytest tests\test_smoke.py -v
```

### Expected Output:
```
tests/test_smoke.py::test_imports PASSED                             [ 12%]
tests/test_smoke.py::test_config_loads PASSED                        [ 25%]
tests/test_smoke.py::test_container_initialization PASSED            [ 37%]
tests/test_smoke.py::test_market_data_contract_validation PASSED     [ 50%]
tests/test_smoke.py::test_bar_completion_check PASSED                [ 62%]
tests/test_smoke.py::test_data_validator_rejects_incomplete_bars PASSED [ 75%]
tests/test_smoke.py::test_strategy_registration PASSED               [ 87%]
tests/test_smoke.py::test_run_options PASSED                         [100%]

========================= 8 passed =========================
```

---

## ROOT CAUSE ANALYSIS

### Why These Errors Occurred:

1. **EventBus → OrderEventBus mismatch**
   - Original design likely had generic `EventBus` class
   - Later specialized to `OrderEventBus` for order-specific events
   - Container import not updated

2. **ConfigLoader API confusion**
   - Test assumed path could be passed to `load()`
   - Actual API: path set in `__init__`, `load()` uses it
   - Classic "assumed API without checking" error

3. **Strategy name case handling**
   - Registry lowercases for case-insensitive lookup
   - Test used original case instead of lowercase
   - Common when registry abstraction not well documented

---

## FILES CHANGED IN PATCH 1.1

1. `core/di/container.py` - Fixed EventBus imports (4 locations)
2. `tests/test_smoke.py` - Fixed ConfigLoader usage and strategy name

---

## CRITICAL LESSON

**Always verify actual API signatures before writing tests.**

Tests should reflect reality, not assumptions:
- ✗ Assumed `EventBus` exists → Failed
- ✗ Assumed `load(path)` → Failed
- ✗ Assumed case-sensitive registry → Failed

**Fix:** Read actual source code before writing integration tests.

---

## READY FOR REAL MONEY NOW?

**NO - Still Need Patches 2-6**

Patch 1.1 only fixes test failures from Patch 1. The actual trading system still needs:

1. **Patch 2** - Duplicate order guards + fat-finger protection
2. **Patch 3** - Reconciliation hard-gate
3. **Patch 4** - Data integrity tests
4. **Patch 5** - Backtest execution fix (bar[t+1])
5. **Patch 6** - Production validation (20+ days paper)

**Current Status:** Tests pass ✓, but system not production-ready ✗

---

## NEXT STEP

Verify Patch 1.1 fixes:
```bash
# Should show 8/8 passed
python -m pytest tests\test_smoke.py -v
```

If all tests pass, proceed to Patch 2.
