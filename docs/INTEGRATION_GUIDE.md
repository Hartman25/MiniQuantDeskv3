# INTEGRATION GUIDE - HOW TO USE NEW FEATURES

**Date:** January 24, 2026  
**Status:** All 6 critical features implemented  
**Next Step:** Integrate into production Container

---

## OVERVIEW

You now have 6 critical new systems ready to integrate:

1. **Clock Abstraction** - Testable time
2. **Throttler** - Rate limiting
3. **OrderTracker** - Lifecycle tracking
4. **Protections** - Circuit breakers
5. **UserStreamTracker** - Real-time WebSocket
6. **Symbol Properties** - Order validation

**Total Code:** ~2,900 lines of production-grade code

---

## STEP 1: UPDATE CONTAINER

### Location: `core/di/container.py`

```python
from core.time import get_clock, Clock
from core.net import create_combined_throttler, Throttler
from core.state.order_tracker import OrderTracker
from core.risk.protections import create_default_protections, ProtectionManager
from core.realtime import UserStreamTracker
from core.market import SymbolPropertiesCache, SecurityCache

class Container:
    """Enhanced DI container with new systems"""
    
    def __init__(self, config: dict):
        self.config = config
        
        # Core systems (in order of dependency)
        self._clock: Optional[Clock] = None
        self._throttler: Optional[Throttler] = None
        self._symbol_props: Optional[SymbolPropertiesCache] = None
        self._security_cache: Optional[SecurityCache] = None
        self._order_tracker: Optional[OrderTracker] = None
        self._protections: Optional[ProtectionManager] = None
        self._user_stream: Optional[UserStreamTracker] = None
        
        # Initialize in order
        self._init_time()
        self._init_net()
        self._init_market()
        self._init_state()
        self._init_risk()
        self._init_realtime()
    
    def _init_time(self):
        """Initialize time system"""
        self._clock = get_clock(self.config)
        logger.info("Clock initialized")
    
    def _init_net(self):
        """Initialize network throttling"""
        self._throttler = create_combined_throttler()
        logger.info("Throttler initialized")
    
    def _init_market(self):
        """Initialize market data systems"""
        # Symbol properties needs broker connector
        self._symbol_props = SymbolPropertiesCache(self.broker)
        self._security_cache = SecurityCache(self._symbol_props)
        logger.info("Market systems initialized")
    
    def _init_state(self):
        """Initialize state tracking"""
        # Existing OrderStateMachine
        self._order_machine = OrderStateMachine(...)
        
        # NEW: OrderTracker
        self._order_tracker = OrderTracker()
        logger.info("State tracking initialized")
    
    def _init_risk(self):
        """Initialize risk management"""
        # Existing PreTradeRiskGate
        self._risk_gate = PreTradeRiskGate(...)
        
        # NEW: Protections
        self._protections = create_default_protections()
        logger.info("Risk systems initialized")
    
    def _init_realtime(self):
        """Initialize real-time streams"""
        self._user_stream = UserStreamTracker(
            api_key=self.config['alpaca']['api_key'],
            api_secret=self.config['alpaca']['api_secret'],
            is_paper=self.config['account']['mode'] == 'paper'
        )
        
        # Wire handlers
        self._user_stream.on_trade_update(self._handle_trade_update)
        self._user_stream.on_account_update(self._handle_account_update)
        
        logger.info("Real-time stream initialized")
    
    async def _handle_trade_update(self, update: dict):
        """Handle trade update from WebSocket"""
        event = update.get('event')
        order_data = update.get('order', {})
        
        client_order_id = order_data.get('client_order_id')
        
        # Update OrderTracker
        if event == 'fill' or event == 'partial_fill':
            # Create fill event
            from core.state.order_tracker import FillEvent
            from decimal import Decimal
            
            fill = FillEvent(
                timestamp=self._clock.now(),
                quantity=Decimal(str(order_data.get('filled_qty', 0))),
                price=Decimal(str(order_data.get('filled_avg_price', 0))),
                commission=Decimal('0')  # Calculate from order
            )
            
            self._order_tracker.process_fill(client_order_id, fill)
        else:
            # Other status update
            self._order_tracker.process_order_update(client_order_id, {
                'status': order_data.get('status'),
                'exchange_order_id': order_data.get('id'),
                'filled_qty': order_data.get('filled_qty')
            })
    
    async def _handle_account_update(self, update: dict):
        """Handle account update from WebSocket"""
        # Update account state
        logger.info(f"Account update: cash={update.get('cash')}, bp={update.get('buying_power')}")
    
    # Properties for access
    @property
    def clock(self) -> Clock:
        return self._clock
    
    @property
    def throttler(self) -> Throttler:
        return self._throttler
    
    @property
    def order_tracker(self) -> OrderTracker:
        return self._order_tracker
    
    @property
    def protections(self) -> ProtectionManager:
        return self._protections
    
    @property
    def user_stream(self) -> UserStreamTracker:
        return self._user_stream
    
    @property
    def security_cache(self) -> SecurityCache:
        return self._security_cache
```

---

## STEP 2: REPLACE datetime.now() CALLS

### Find and Replace Pattern

```bash
# Search for:
datetime.now(timezone.utc)
datetime.now()
datetime.utcnow()

# Replace with:
self.clock.now()
```

### Critical Files to Update:

1. **`core/state/order_machine.py`**
```python
# Before:
timestamp = datetime.now(timezone.utc)

# After:
timestamp = self.clock.now()
```

2. **`core/state/position_store.py`**
```python
# Before:
entry_time = datetime.now(timezone.utc)

# After:
entry_time = self.clock.now()
```

3. **`core/brokers/alpaca_connector.py`**
```python
# Before:
timestamp = datetime.now(timezone.utc)

# After:
timestamp = self.clock.now()
```

4. **`strategies/vwap_mean_reversion.py`**
```python
# Before:
signal_time = datetime.now(timezone.utc)

# After:
signal_time = self.clock.now()
```

---

## STEP 3: WRAP API CALLS WITH THROTTLER

### Broker Calls

```python
# core/brokers/alpaca_connector.py

class AlpacaConnector:
    def __init__(self, config, throttler):
        self.throttler = throttler
        # ...
    
    async def submit_order(self, symbol, qty, side, order_type, **kwargs):
        """Submit order with throttling"""
        
        # Wrap in throttler
        return await self.throttler.execute(
            'alpaca_orders',
            self._submit_order_impl,
            symbol, qty, side, order_type, **kwargs
        )
    
    async def _submit_order_impl(self, symbol, qty, side, order_type, **kwargs):
        """Actual implementation"""
        # Original submit_order code here
        pass
    
    async def get_account(self):
        """Get account with throttling"""
        return await self.throttler.execute(
            'alpaca_account',
            self._get_account_impl
        )
    
    async def _get_account_impl(self):
        """Actual implementation"""
        # Original get_account code here
        pass
```

### Data Provider Calls

```python
# core/data/polygon_provider.py

class PolygonProvider:
    def __init__(self, api_key, throttler):
        self.throttler = throttler
        # ...
    
    async def get_bars(self, symbol, timeframe, start, end):
        """Get bars with throttling"""
        return await self.throttler.execute(
            'polygon_bars',
            self._get_bars_impl,
            symbol, timeframe, start, end
        )
```

---

## STEP 4: INTEGRATE ORDER TRACKING

### Update Order Creation

```python
# In your order execution flow:

from core.state.order_tracker import InFlightOrder, OrderSide, OrderType
from decimal import Decimal

async def create_order(self, signal):
    """Create and track order"""
    
    # Create order
    order_id = f"ORD_{uuid.uuid4().hex[:8]}"
    
    # Create InFlightOrder
    in_flight = InFlightOrder(
        client_order_id=order_id,
        symbol=signal['symbol'],
        quantity=Decimal(str(signal['quantity'])),
        side=OrderSide.BUY if signal['side'] == 'BUY' else OrderSide.SELL,
        order_type=OrderType.MARKET,
        strategy_id=signal.get('strategy_id'),
        created_at=self.clock.now()
    )
    
    # Start tracking
    self.order_tracker.start_tracking(in_flight)
    
    # Submit to broker (with throttling)
    broker_order = await self.throttler.execute(
        'alpaca_orders',
        self.broker.submit_order,
        ...
    )
    
    # Update with broker ID
    self.order_tracker.process_order_update(order_id, {
        'exchange_order_id': broker_order['id'],
        'status': broker_order['status'],
        'submitted_at': self.clock.now()
    })
```

### Daily Reconciliation

```python
async def reconcile_orders(self):
    """Daily reconciliation check"""
    
    # Get broker orders
    broker_orders = await self.broker.get_open_orders()
    broker_dict = {o['id']: o for o in broker_orders}
    
    # Check for orphans
    orphans = self.order_tracker.get_orphaned_orders(broker_dict)
    if orphans:
        logger.error(f"ORPHAN ORDERS DETECTED: {orphans}")
        # Alert to Discord
        await self.discord.send_alert("🚨 Orphan orders found!", orphans)
    
    # Check for shadows
    shadows = self.order_tracker.get_shadow_orders(broker_dict)
    if shadows:
        logger.error(f"SHADOW ORDERS DETECTED: {shadows}")
        # Alert to Discord
        await self.discord.send_alert("⚠️ Shadow orders found!", shadows)
```

---

## STEP 5: ADD PROTECTION CHECKS

### Before Risk Gate

```python
async def process_signal(self, signal):
    """Process trading signal with protections"""
    
    symbol = signal['symbol']
    
    # STEP 1: Check protections FIRST
    protection_result = self.protections.check(
        symbol=symbol,
        current_trades=self.position_store.get_all(),
        completed_trades=self.get_recent_closed_trades()
    )
    
    if protection_result.is_protected:
        logger.warning(
            f"Trade blocked by protection: {protection_result.reason}",
            extra={
                'symbol': symbol,
                'reason': protection_result.reason,
                'until': protection_result.until,
                'trigger': protection_result.trigger.value
            }
        )
        
        # Send to Discord
        await self.discord.send_message(
            channel='trading',
            message=f"🛡️ Protection active: {protection_result.reason}"
        )
        
        return  # BLOCKED
    
    # STEP 2: Check existing risk gate
    approved, reason = self.risk_gate.check(signal)
    
    if not approved:
        logger.warning(f"Trade blocked by risk gate: {reason}")
        return
    
    # STEP 3: Execute
    await self.execute_signal(signal)
```

---

## STEP 6: VALIDATE ORDERS WITH SYMBOL PROPERTIES

### Before Submitting Orders

```python
async def execute_signal(self, signal):
    """Execute signal with validation"""
    
    symbol = signal['symbol']
    quantity = signal['quantity']
    price = signal.get('price')  # None for market orders
    side = signal['side']
    
    # Load security (or get from cache)
    security = await self.security_cache.get_or_create(symbol)
    
    # Validate order
    is_valid, reason = security.validate_order(
        quantity=quantity,
        price=price,
        side=side
    )
    
    if not is_valid:
        logger.error(
            f"Invalid order: {reason}",
            extra={'symbol': symbol, 'qty': quantity, 'price': price}
        )
        return
    
    # Round values
    if price:
        price = security.round_price(price)
    quantity = security.round_quantity(quantity)
    
    # Submit
    order = await self.broker.submit_order(
        symbol=symbol,
        qty=quantity,
        side=side,
        price=price
    )
```

---

## STEP 7: START USER STREAM

### In Main Entry Point

```python
# entry_paper.py or entry_live.py

async def main():
    """Main entry point"""
    
    # Initialize container
    container = Container(config)
    
    # Start user stream
    await container.user_stream.start()
    
    try:
        # Run trading loop
        await trading_loop(container)
    finally:
        # Stop user stream
        await container.user_stream.stop()
```

---

## TESTING CHECKLIST

### Clock Tests
```python
def test_clock_abstraction():
    # Test real-time clock
    clock = RealTimeClock()
    now = clock.now()
    assert now.tzinfo == timezone.utc
    
    # Test backtest clock
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bt_clock = BacktestClock(start)
    assert bt_clock.now() == start
    
    bt_clock.advance(timedelta(hours=1))
    assert bt_clock.now() == start + timedelta(hours=1)
```

### Throttler Tests
```python
async def test_throttler():
    from core.net import Throttler, RateLimit
    
    throttler = Throttler({'test': RateLimit(5, 1.0)})
    
    # Should allow 5 immediately
    for i in range(5):
        await throttler.execute('test', lambda x: x, i)
    
    # 6th should wait
    import time
    start = time.time()
    await throttler.execute('test', lambda: None)
    elapsed = time.time() - start
    assert elapsed >= 0.9
```

### OrderTracker Tests
```python
def test_orphan_detection():
    tracker = OrderTracker()
    
    # Broker has order we don't know about
    broker_orders = {'BROKER_001': {'status': 'FILLED'}}
    
    orphans = tracker.get_orphaned_orders(broker_orders)
    assert 'BROKER_001' in orphans
```

### Protection Tests
```python
def test_stoploss_guard():
    from core.risk.protections import StoplossGuard
    from datetime import timedelta
    
    guard = StoplossGuard(
        max_stoplosses=3,
        lookback_period=timedelta(hours=1),
        cooldown_duration=timedelta(hours=1)
    )
    
    # Create 3 losing trades
    trades = [
        MockTrade(symbol='SPY', profit=-100, close_timestamp=clock.now()),
        MockTrade(symbol='SPY', profit=-100, close_timestamp=clock.now()),
        MockTrade(symbol='SPY', profit=-100, close_timestamp=clock.now()),
    ]
    
    result = guard.check(symbol='SPY', completed_trades=trades)
    assert result.is_protected
```

### Symbol Properties Tests
```python
async def test_symbol_validation():
    # Create mock properties
    props = SymbolProperties(
        symbol='SPY',
        min_price_increment=Decimal('0.01'),
        lot_size=1,
        min_order_size=1
    )
    
    # Valid order
    is_valid, reason = props.validate_order(
        quantity=10,
        price=Decimal('450.13'),
        side='BUY'
    )
    assert is_valid
    
    # Invalid price increment
    is_valid, reason = props.validate_order(
        quantity=10,
        price=Decimal('450.127'),  # Not valid increment
        side='BUY'
    )
    assert not is_valid
```

---

## MONITORING & DEBUGGING

### Add Status Endpoint

```python
@app.get("/status")
async def get_status():
    """Get system status"""
    return {
        'clock': {
            'current_time': container.clock.now().isoformat(),
            'is_market_hours': container.clock.is_market_hours()
        },
        'throttler': container.throttler.get_stats(),
        'order_tracker': container.order_tracker.get_stats(),
        'protections': container.protections.get_all_statuses(),
        'user_stream': container.user_stream.get_stats(),
        'securities': container.security_cache.get_stats()
    }
```

### Discord Notifications

```python
# Add to Discord channels:
# - #protections (when protection triggers)
# - #order-drift (when orphans/shadows detected)
# - #throttle-warnings (when rate limits hit)

async def monitor_systems(container):
    """Periodic system monitoring"""
    while True:
        await asyncio.sleep(60)  # Every minute
        
        # Check user stream health
        if not container.user_stream.is_connected():
            await discord.send_alert("⚠️ User stream disconnected!")
        
        # Check for orphans
        broker_orders = await container.broker.get_open_orders()
        broker_dict = {o['id']: o for o in broker_orders}
        orphans = container.order_tracker.get_orphaned_orders(broker_dict)
        
        if orphans:
            await discord.send_alert(f"🚨 {len(orphans)} orphan orders!")
        
        # Check throttler stats
        stats = container.throttler.get_stats()
        for limit_id, stat in stats.items():
            if stat['total_waits'] > 10:
                await discord.send_message(
                    'system',
                    f"⏳ Throttler '{limit_id}' hitting limits: {stat['total_waits']} waits"
                )
```

---

## GRADUAL ROLLOUT PLAN

### Week 1: Test in Experimental
1. Add to Container
2. Write integration tests
3. Test with paper trading
4. Monitor for issues

### Week 2: Deploy Clock + Throttler
1. Copy clock and throttler to production
2. Replace datetime.now() calls
3. Wrap API calls
4. Monitor rate limits

### Week 3: Deploy OrderTracker + Protections
1. Copy order tracking
2. Add protection checks
3. Monitor for orphans/shadows
4. Test protection triggers

### Week 4: Deploy UserStream + SymbolProps
1. Start WebSocket stream
2. Add symbol validation
3. Monitor fill latency
4. Full system validation

---

**Integration Status:** Ready to begin  
**Risk Level:** Medium (test thoroughly in experimental first)  
**Expected Impact:** Major improvement in safety and reliability

🚀 **All components ready. Start integration in experimental, then gradual rollout to production.**
