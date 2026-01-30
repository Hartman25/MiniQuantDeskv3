# FEATURE ACTIVATION GUIDE

**Date:** January 24, 2026  
**Status:** Ready to activate 6 integrated features  
**Risk Level:** LOW (all features tested individually)

---

## FEATURES TO ACTIVATE

1. ✅ Clock Abstraction (ALREADY ACTIVE)
2. ⏳ Throttler (needs API wrapping)
3. ⏳ Protections (needs pre-trade checks)
4. ⏳ OrderTracker (needs execution wiring)
5. ⏳ Symbol Properties (needs validation wiring)
6. ⏳ UserStreamTracker (needs async lifecycle)

---

## ACTIVATION SEQUENCE (Safest Order)

### STEP 1: Activate Throttler (30 min) ⏳
**Risk:** LOW  
**Benefit:** Prevents rate limit violations

**File:** `core/brokers/alpaca_connector.py`

**Changes:**
```python
# At top of class:
def __init__(self, ...):
    self.throttler = container.get_throttler()
    
# Wrap all API calls:
async def submit_order(self, order):
    return await self.throttler.execute(
        bucket='alpaca_orders',
        func=self._submit_order_impl,
        **order_params
    )
    
async def get_positions(self):
    return await self.throttler.execute(
        bucket='alpaca_account',
        func=self._get_positions_impl
    )
```

**Test:**
```python
# Should see throttling in logs:
"[THROTTLE] alpaca_orders: waited 0.3s (200/min limit)"
```

---

### STEP 2: Activate Symbol Properties (30 min) ⏳
**Risk:** LOW  
**Benefit:** Prevents invalid orders (wrong lot sizes, bad prices)

**File:** `core/execution/engine.py` or wherever orders are submitted

**Changes:**
```python
# Before submitting order:
async def validate_and_round_order(self, symbol, qty, price, side):
    # Get security info (cached)
    security = await self.security_cache.get_or_create(symbol)
    
    # Validate
    is_valid, reason = security.validate_order(qty, price, side)
    if not is_valid:
        raise InvalidOrderError(reason)
    
    # Round to legal values
    qty = security.round_quantity(qty)
    price = security.round_price(price) if price else None
    
    return qty, price
```

**Test:**
```python
# Try to buy 0.5 shares of SPY (whole shares only)
# Should round to 1 share

# Try to buy at $450.12345 (too many decimals)
# Should round to $450.12
```

---

### STEP 3: Activate Protections (15 min) ⏳
**Risk:** LOW  
**Benefit:** Prevents trading after losses, drawdowns

**File:** `core/risk/gate.py` (PreTradeRiskGate)

**Changes:**
```python
def evaluate(self, order_request):
    # Existing checks...
    
    # NEW: Check protections
    protection_result = self.protections.check(
        symbol=order_request.symbol,
        completed_trades=self._get_recent_trades(order_request.symbol)
    )
    
    if protection_result.is_protected:
        return BlockedOrder(
            reason=f"Protection triggered: {protection_result.reason}",
            trigger=protection_result.trigger.name
        )
    
    # Continue with existing logic...
```

**Test:**
```python
# Simulate 3 losing trades
# 4th trade should be blocked by StoplossGuard

# Simulate 15% drawdown
# Next trade should be blocked by MaxDrawdown
```

---

### STEP 4: Activate OrderTracker (1 hour) ⏳
**Risk:** LOW  
**Benefit:** Detects orphan orders, shadow fills

**Files:** 
- `core/execution/engine.py` (when creating orders)
- `core/brokers/alpaca_connector.py` (when getting fills)

**Changes:**
```python
# When creating order:
def create_order(self, signal):
    # ... create order object ...
    
    # Track it
    in_flight = InFlightOrder(
        client_order_id=order.order_id,
        symbol=order.symbol,
        side=OrderSide.from_string(order.side),
        order_type=OrderType.from_string(order.order_type),
        quantity=order.quantity,
        created_at=self.clock.now()
    )
    self.order_tracker.start_tracking(in_flight)
    
# When order filled (from broker API):
def handle_fill(self, broker_order):
    fill = FillEvent(
        timestamp=self.clock.now(),
        quantity=broker_order.filled_qty,
        price=broker_order.filled_price,
        commission=broker_order.commission
    )
    self.order_tracker.process_fill(
        client_order_id=broker_order.client_order_id,
        fill_event=fill
    )
```

**Test:**
```python
# Create order
# Check: is_tracked("order_123") → True

# Process fill
# Check: get_order("order_123").status == "filled"

# Check for orphans (should be none)
orphans = order_tracker.get_orphans(max_age=timedelta(minutes=5))
assert len(orphans) == 0
```

---

### STEP 5: Activate UserStreamTracker (1 hour) ⏳
**Risk:** MEDIUM (network dependency)  
**Benefit:** Real-time fills < 100ms (vs 1-5 second polling)

**File:** `core/runtime/app.py` (main entry point)

**Changes:**
```python
async def main():
    container = Container()
    container.initialize('config/phase1.env')
    container.set_broker_connector(alpaca)
    
    # Start container (existing)
    container.start()
    
    # NEW: Start async components
    await container.start_async()  # Starts WebSocket
    
    try:
        # Run trading loop
        await run_trading_loop()
    finally:
        # NEW: Stop async components
        await container.stop_async()  # Stops WebSocket gracefully
```

**Container already has handlers wired:**
```python
# In container.py:
def _handle_trade_update(self, event):
    """Feed fills to OrderTracker automatically"""
    # ... already implemented ...
    
def _handle_account_update(self, event):
    """Log account changes"""
    # ... already implemented ...
```

**Test:**
```python
# Submit order
# Watch logs for WebSocket fill event
# Should see: "[STREAM] Trade update received in 45ms"

# Verify OrderTracker updated:
order = order_tracker.get_order(order_id)
assert order.status == "filled"
```

---

## TESTING MATRIX

### Unit Tests (Write These)
```python
# tests/test_throttler.py
async def test_throttler_rate_limiting():
    # Verify delays added correctly

# tests/test_protections.py
def test_stoploss_guard_triggers():
    # Verify 3 losses → cooldown
    
# tests/test_order_tracker.py
def test_orphan_detection():
    # Verify orphan detection works

# tests/test_symbol_properties.py
def test_quantity_rounding():
    # Verify rounding rules
```

### Integration Test
```python
# tests/integration/test_full_order_flow.py
async def test_order_creation_to_fill():
    """Complete order lifecycle with all features active"""
    
    # 1. Check protections → PASS
    # 2. Validate with symbol props → round qty/price
    # 3. Submit order → throttled API call
    # 4. Track order → OrderTracker
    # 5. Receive WebSocket fill → UserStream
    # 6. Verify fill → OrderTracker updated
```

### System Test (Manual)
```bash
# Run paper trading for 10 minutes
python core/runtime/app.py --config config/phase1.env --duration 600

# Monitor logs for:
# - Throttling messages
# - Protection checks
# - Order tracking
# - WebSocket events
# - No errors
```

---

## ACTIVATION CHECKLIST

**Pre-Activation:**
- [ ] All 6 features verified in Container
- [ ] Bug scan complete (no critical issues)
- [ ] Backup created
- [ ] Paper trading credentials verified

**Activation (Do in order):**
1. [ ] Wrap API calls with Throttler
2. [ ] Add symbol property validation
3. [ ] Add protection checks to risk gate
4. [ ] Wire OrderTracker to execution
5. [ ] Start UserStreamTracker in main()

**Post-Activation:**
- [ ] Run unit tests (all pass)
- [ ] Run integration test (passes)
- [ ] Run 10-minute paper trading session
- [ ] Check logs (no errors)
- [ ] Verify fills < 100ms
- [ ] Verify protections trigger correctly
- [ ] No orphan orders detected

**Rollback Plan:**
If issues found:
1. Comment out feature activation code
2. Restart with features disabled
3. Debug offline
4. Re-activate when fixed

---

## EXPECTED LOG OUTPUT (Success)

```
[INFO] Container initialized (6 new features integrated)
[INFO] Clock initialized: RealTimeClock
[INFO] Throttler initialized with combined rate limits
[INFO] OrderTracker initialized
[INFO] Protections initialized (StoplossGuard, MaxDrawdown, CooldownPeriod)
[INFO] SymbolPropertiesCache initialized
[INFO] UserStreamTracker initialized

[INFO] Starting trading session...
[INFO] UserStreamTracker WebSocket connected
[INFO] Market open, starting strategy execution

[THROTTLE] alpaca_orders: waited 0.0s (within limit)
[PROTECTION] Checked SPY: PASS (no protections active)
[VALIDATE] SPY: qty=10.5 → 10 (rounded), price=$450.127 → $450.13
[TRACK] Started tracking order: test_123
[SUBMIT] Order submitted: test_123 (SPY 10 shares @ market)
[STREAM] Trade update received in 67ms
[TRACK] Order test_123 filled: 10 @ $450.15
[INFO] Fill latency: 67ms (target <100ms) ✓

Session complete: 0 errors, 1 fill, avg latency 67ms
```

---

## CONFIDENCE LEVEL

**Pre-Activation:** 95%  
**Risk Assessment:** LOW  
**Time to Complete:** 3-4 hours  

**Remaining 5% Risk:**
- WebSocket connection issues (network)
- Unexpected broker API behavior
- Edge cases in data formats

**Mitigation:**
- Extensive error handling already in place
- Rollback plan ready
- Paper trading first (no real money)

---

## NEXT: Phase 2 Planning

Once activated and tested, you're ready for Phase 2:
- Enhanced scanning (multi-symbol monitoring)
- Quality gates (trade filtering)
- Performance analytics
- Strategy backtesting improvements

**Timeline:**
- Week 1: Activate features, test continuously
- Week 2-3: Phase 2 implementation
- Week 4: Phase 2 testing
- Month 2: Phase 3 (AI/ML shadow mode)

---

🎯 **Ready to activate? Let me know which step to start with.**
