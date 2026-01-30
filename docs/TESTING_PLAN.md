# COMPREHENSIVE TESTING & BULLETPROOFING PLAN

**Date:** January 24, 2026  
**Goal:** Make Phase 1 bulletproof before Phase 2  
**Approach:** Test + Activate simultaneously

---

## PART 1: CODE AUDIT FINDINGS

### CRITICAL: datetime.utcnow() Usage (44 instances found)

**Files requiring Clock integration:**
1. `core/state/order.py` - Line 58
2. `core/portfolio/manager.py` - Line 153
3. `core/state/position_store.py` - Line 235
4. `core/strategies/vwap_mean_reversion.py` - Lines 52, 66
5. `core/logging/config.py` - Lines 46, 74
6. `core/logging/formatters.py` - Line 33+
7. `core/state/transaction_log.py` - Line 113

**Impact:** Time bugs in backtesting, inconsistent timestamps  
**Priority:** HIGH  
**Fix:** Inject clock via Container, replace all instances

---

## PART 2: SYSTEMATIC TESTING STRATEGY

### Phase 1A: Core Infrastructure Tests ✅

**1. Container Integration Test**
```python
def test_container_initialization():
    """Verify all 6 features initialize properly"""
    container = Container()
    container.initialize("config/phase1.env")
    
    # Test new features
    assert container.get_clock() is not None
    assert container.get_throttler() is not None
    assert container.get_order_tracker() is not None
    assert container.get_protections() is not None
    
    # Test existing features still work
    assert container.get_order_machine() is not None
    assert container.get_position_store() is not None
    assert container.get_risk_gate() is not None
```

**2. Clock Abstraction Test**
```python
def test_clock_modes():
    """Test real-time vs backtest clock"""
    # Real-time clock
    real_clock = RealTimeClock()
    t1 = real_clock.now()
    time.sleep(0.1)
    t2 = real_clock.now()
    assert t2 > t1
    
    # Backtest clock
    backtest_clock = BacktestClock(start_time=datetime(2024, 1, 1))
    t1 = backtest_clock.now()
    backtest_clock.advance(timedelta(hours=1))
    t2 = backtest_clock.now()
    assert t2 == t1 + timedelta(hours=1)
```

**3. Throttler Test**
```python
async def test_throttler_rate_limiting():
    """Verify rate limits work correctly"""
    throttler = create_combined_throttler()
    
    # Should NOT throttle first call
    start = time.time()
    await throttler.execute('alpaca_orders', dummy_api_call)
    elapsed = time.time() - start
    assert elapsed < 0.1  # No delay
    
    # Rapid calls should be throttled
    calls = []
    for i in range(10):
        start = time.time()
        await throttler.execute('alpaca_orders', dummy_api_call)
        calls.append(time.time() - start)
    
    # Later calls should have delays (200/min = 0.3s between calls)
    assert max(calls) > 0.2
```

**4. OrderTracker Test**
```python
def test_order_tracker_lifecycle():
    """Test order tracking from creation to fill"""
    tracker = OrderTracker()
    
    # Create order
    order = InFlightOrder(
        client_order_id="test_123",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        created_at=datetime.now(timezone.utc)
    )
    tracker.start_tracking(order)
    
    # Process fill
    fill = FillEvent(
        timestamp=datetime.now(timezone.utc),
        quantity=Decimal("10"),
        price=Decimal("450.00"),
        commission=Decimal("0")
    )
    tracker.process_fill("test_123", fill)
    
    # Verify state
    assert tracker.get_order("test_123").status == "filled"
    assert tracker.get_order("test_123").average_fill_price == Decimal("450.00")
```

**5. Protections Test**
```python
def test_protection_triggers():
    """Test each protection type triggers correctly"""
    manager = create_default_protections()
    
    # Test StoplossGuard (3 losses → cooldown)
    losing_trades = [
        MockTrade(pnl=-100, symbol="SPY"),
        MockTrade(pnl=-100, symbol="SPY"),
        MockTrade(pnl=-100, symbol="SPY"),
    ]
    result = manager.check(symbol="SPY", completed_trades=losing_trades)
    assert result.is_protected
    assert result.trigger == ProtectionTrigger.STOPLOSS_STREAK
    
    # Test MaxDrawdown (15% DD → halt)
    # Test CooldownPeriod ($500 loss → pause)
```

### Phase 1B: Integration Tests

**6. Full Order Flow Test**
```python
async def test_complete_order_flow():
    """Test order: create → validate → submit → track → fill → close"""
    container = Container()
    container.initialize("config/phase1.env")
    
    # 1. Check protections (should pass)
    protections = container.get_protections()
    result = protections.check(symbol="SPY", completed_trades=[])
    assert not result.is_protected
    
    # 2. Validate with symbol properties
    security_cache = container.get_security_cache()
    security = await security_cache.get_or_create("SPY")
    qty = Decimal("10.5")
    price = Decimal("450.127")
    
    # Should round properly
    rounded_qty = security.round_quantity(qty)
    rounded_price = security.round_price(price)
    assert rounded_qty == Decimal("10")  # Whole shares for SPY
    assert rounded_price == Decimal("450.13")  # Penny rounding
    
    # 3. Track order
    tracker = container.get_order_tracker()
    # ... continue through full flow
```

**7. WebSocket → OrderTracker Pipeline Test**
```python
async def test_user_stream_integration():
    """Test WebSocket fills feed into OrderTracker"""
    container = Container()
    container.initialize("config/phase1.env")
    container.set_broker_connector(mock_broker)
    
    # Start user stream
    await container.start_async()
    
    # Simulate WebSocket fill event
    mock_fill = {
        'event': 'fill',
        'order': {
            'client_order_id': 'test_123',
            'filled_qty': 10,
            'filled_avg_price': 450.00
        }
    }
    
    # Verify OrderTracker received it
    # ... check tracker state
```

### Phase 1C: System Tests

**8. Paper Trading Integration Test**
```python
def test_paper_trading_session():
    """Run actual paper trading for 10 minutes"""
    # 1. Start system
    # 2. Let it run for market open
    # 3. Verify:
    #    - No crashes
    #    - Clock working
    #    - Throttler preventing rate limits
    #    - Protections not false-triggering
    #    - Orders tracked properly
    #    - WebSocket fills < 100ms
```

---

## PART 3: ACTIVATION SEQUENCE

### Step 1: Replace datetime.utcnow() (HIGH PRIORITY)
**Files:** 7 files, 44 instances
**Time:** 1-2 hours
**Risk:** MEDIUM (breaks if done wrong)

### Step 2: Wrap API Calls with Throttler
**Files:** alpaca_connector.py, pipeline.py
**Time:** 1 hour
**Risk:** LOW (failsafe = no throttling)

### Step 3: Add Protection Checks
**Files:** Pre-trade flow
**Time:** 30 minutes
**Risk:** LOW (can disable if too aggressive)

### Step 4: Wire OrderTracker
**Files:** Execution engine
**Time:** 1 hour
**Risk:** LOW (passive tracking)

### Step 5: Enable Symbol Validation
**Files:** Order submission
**Time:** 30 minutes
**Risk:** LOW (validation prevents bad orders)

### Step 6: Start UserStream
**Files:** Main entry
**Time:** 30 minutes
**Risk:** MEDIUM (network dependency)

---

## PART 4: BUG HUNTING CHECKLIST

### Common Python Bugs to Check:
- [ ] Unhandled exceptions
- [ ] Resource leaks (files, connections, threads)
- [ ] Race conditions in threading
- [ ] SQL injection vulnerabilities
- [ ] Type errors (Decimal vs float mixing)
- [ ] Timezone issues
- [ ] Off-by-one errors
- [ ] Division by zero
- [ ] None checks missing
- [ ] Circular imports

### MiniQuantDesk-Specific Risks:
- [ ] Look-ahead bias in backtesting
- [ ] Order state machine bugs
- [ ] Position tracking drift
- [ ] Broker reconciliation failures
- [ ] Data staleness not detected
- [ ] Kill switch not working
- [ ] PDT protection bypass
- [ ] Missing trade logging
- [ ] Discord webhook failures (silent)
- [ ] Config validation missing

---

## PART 5: TESTING TOOLS

### Unit Test Framework:
```python
# tests/test_core.py
import pytest
from core.di.container import Container

@pytest.fixture
def container():
    c = Container()
    c.initialize("config/test.env")
    return c

def test_clock(container):
    clock = container.get_clock()
    assert clock.now() is not None
```

### Integration Test Framework:
```python
# tests/integration/test_order_flow.py
async def test_full_order_lifecycle(container, mock_broker):
    # End-to-end test
    pass
```

### System Test Script:
```bash
# tests/system_test.sh
python core/runtime/app.py --config config/test_paper.env --duration 600
# Let run 10 minutes, check logs for errors
```

---

## EXECUTION PLAN

**Week 1: Fix + Test Core**
- Day 1-2: Replace datetime.utcnow() in all files
- Day 3: Write unit tests for 6 new features
- Day 4: Run unit tests, fix bugs
- Day 5: Integration tests

**Week 2: Activate + Validate**
- Day 1: Wrap API calls with throttler
- Day 2: Add protection checks
- Day 3: Wire OrderTracker
- Day 4: Enable symbol validation
- Day 5: Start UserStream, full system test

**Week 3: Stress Test**
- Run paper trading continuously
- Monitor for issues
- Fix bugs as found
- Tune protections

**Week 4: Final Validation**
- Full regression test
- Verify all safety features work
- Document any limitations
- **DECIDE:** Ready for Phase 2?

---

## SUCCESS CRITERIA

Phase 1 is "bulletproof" when:
- [ ] All unit tests pass (100%)
- [ ] All integration tests pass (100%)
- [ ] 1 week of paper trading with zero crashes
- [ ] No orphan orders detected
- [ ] No rate limit violations
- [ ] Protections trigger correctly (not too aggressive/weak)
- [ ] WebSocket fills consistently < 100ms
- [ ] Kill switch works
- [ ] Broker reconciliation perfect
- [ ] All datetime.utcnow() replaced

**Only then → Phase 2**

---

**Ready to start?**

I'll begin with:
1. Replacing all datetime.utcnow() calls
2. Writing unit tests
3. Running systematic checks

Sound good?
