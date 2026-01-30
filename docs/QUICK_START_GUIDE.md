# MINIQUANTDESK QUICK START GUIDE
## How to Run Your Trading System (Step-by-Step)

**Last Updated:** January 20, 2026  
**Status:** Phase 1 Complete - Ready for Paper Trading  
**Recommended Path:** Paper Trading → Validation → Live Trading

---

## 📋 PRE-FLIGHT CHECKLIST

Before running for the first time, verify you have:

### 1. Required Accounts & API Keys
- [ ] Alpaca account created (https://alpaca.markets)
- [ ] Paper trading API keys generated
- [ ] Discord bot created (https://discord.com/developers)
- [ ] Discord channels created (7 total - see below)
- [ ] Market data provider API keys (Polygon, Finnhub, or FMP)

### 2. System Requirements
- [ ] Python 3.10+ installed
- [ ] Windows 10/11 (current environment)
- [ ] 2GB+ free RAM
- [ ] 10GB+ free disk space
- [ ] Stable internet connection

### 3. Project Setup
- [ ] All dependencies installed (`pip install -r requirements.txt`)
- [ ] Configuration files present (`config/config.yaml`, `.env`)
- [ ] Log directories created (automatic, but verify)
- [ ] Database directories exist (`data/`)

---

## 🔧 FIRST-TIME SETUP (Do This Once)

### Step 1: Configure Environment Variables

**File:** `_env` (in project root)

```bash
# ALPACA BROKER (Paper Trading)
ALPACA_API_KEY=your_alpaca_paper_api_key_here
ALPACA_SECRET_KEY=your_alpaca_paper_secret_key_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# For Live Trading (DO NOT USE YET - PAPER ONLY):
# ALPACA_BASE_URL=https://api.alpaca.markets

# MARKET DATA PROVIDERS
POLYGON_API_KEY=your_polygon_key_here
FINNHUB_API_KEY=your_finnhub_key_here
FMP_API_KEY=your_fmp_key_here
ALPHA_VANTAGE_API_KEY=your_alphavantage_key_here

# DISCORD NOTIFICATIONS
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_PAPER_CHANNEL_ID=1234567890  # Paper trading channel
DISCORD_LIVE_CHANNEL_ID=1234567891   # Live trading channel
DISCORD_CALENDAR_CHANNEL_ID=1234567892  # Economic calendar
DISCORD_SCANNER_CHANNEL_ID=1234567893   # Scanner results
DISCORD_HEARTBEAT_CHANNEL_ID=1234567894  # Heartbeat
DISCORD_BACKTEST_CHANNEL_ID=1234567895   # Backtests
DISCORD_ALERTS_CHANNEL_ID=1234567896     # System alerts

# LOGGING
LOG_LEVEL=INFO  # DEBUG for detailed logs, INFO for normal
```

**Important:**
- Keep this file SECRET (never commit to git)
- Use PAPER API keys for all testing
- Discord channel IDs are numeric, get from Discord (right-click channel → Copy ID)

### Step 2: Configure Trading Settings

**File:** `config/config.yaml`

```yaml
# ACCOUNT SETTINGS
account:
  initial_capital: 10000.00  # Starting capital (paper money)
  mode: paper                # paper or live (USE PAPER ONLY FOR NOW)

# RISK LIMITS (CRITICAL SAFETY SETTINGS)
risk:
  daily_loss_limit: 500.00              # Stop trading if lose $500 in one day
  max_position_size: 1000.00            # No single position > $1000
  max_exposure_per_position: 0.20       # Max 20% of capital per position
  max_total_exposure: 0.95              # Max 95% of capital deployed
  min_position_value: 100.00            # Minimum position size
  enable_pdt_protection: true           # Prevent 4th day trade
  max_orders_per_day: 100               # Safety limit

# TRADING HOURS
trading:
  symbols: [SPY, QQQ, IWM]              # Symbols to trade
  timeframe: 1min                       # Data interval
  trading_hours:
    start: "09:30"                      # Market open (ET)
    end: "16:00"                        # Market close (ET)
    timezone: "America/New_York"
  
# STRATEGY SETTINGS
strategies:
  - name: vwap_mean_reversion          # Strategy name
    enabled: true                       # Active or not
    symbols: [SPY]                      # Which symbols
    params:
      lookback_period: 20               # VWAP calculation period
      entry_threshold: 0.02             # 2% from VWAP for entry
      stop_loss_pct: 0.02               # 2% stop loss
      take_profit_pct: 0.015            # 1.5% take profit

# DATA PROVIDERS (in order of preference)
data:
  providers:
    - polygon
    - finnhub
    - fmp
    - alpha_vantage
  cache_enabled: true
  max_cache_size: 10000
  cache_ttl_seconds: 300

# PATHS (auto-created if missing)
paths:
  data_dir: ./data
  logs_dir: ./logs
  cache_dir: ./data/cache
```

**Recommended First Run Settings:**
- `initial_capital: 10000.00` (paper money)
- `daily_loss_limit: 500.00` (5% max daily loss)
- `max_position_size: 1000.00` ($1000 max per position)
- `symbols: [SPY]` (start with one symbol)

### Step 3: Create Discord Channels

In your Discord server, create **7 channels**:

1. **#paper-trading** - All paper trades
2. **#live-trading** - Live trades (future)
3. **#economic-calendar** - Economic events
4. **#scanner-results** - Market opportunities
5. **#heartbeat** - System health (every 5 min)
6. **#backtest-results** - Strategy performance
7. **#system-alerts** - Errors and warnings

**Get Channel IDs:**
1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
2. Right-click each channel → Copy ID
3. Paste into `_env` file

### Step 4: Verify Alpaca API Keys

**Test your connection:**

```python
# Run this quick test:
python -c "
import os
from alpaca.trading.client import TradingClient

api_key = os.getenv('ALPACA_API_KEY')
secret_key = os.getenv('ALPACA_SECRET_KEY')

client = TradingClient(api_key, secret_key, paper=True)
account = client.get_account()
print(f'✓ Connected! Account value: ${account.equity}')
"
```

**Expected output:**
```
✓ Connected! Account value: $100000.00
```

If you see an error, check your API keys in `_env`.

---

## 🚀 RUNNING PAPER TRADING

### Method 1: Command Line (Recommended for First Run)

```bash
# Navigate to project directory
cd C:\Users\Zacha\Desktop\MiniQuantDeskv2

# Activate virtual environment (if using one)
venv\Scripts\activate

# Run paper trading
python entry_paper.py
```

### Method 2: Task Scheduler (For Automated Daily Runs)

**Coming soon** - Not recommended until after manual validation

---

## 📊 WHAT TO EXPECT WHEN RUNNING

### Startup Sequence (Takes 10-30 seconds)

```
1. Loading configuration...
   ✓ Config loaded from config/config.yaml
   ✓ Environment variables loaded from _env

2. Initializing components...
   ✓ Event bus started
   ✓ Data pipeline ready
   ✓ Risk gate active
   ✓ Order state machine initialized
   ✓ Position store connected
   ✓ Transaction log ready

3. Connecting to broker...
   ✓ Connected to Alpaca (PAPER)
   ✓ Account: $100,000.00 buying power

4. Reconciling state...
   ✓ Positions synchronized (0 positions)
   ✓ Orders synchronized (0 pending orders)
   ✓ No discrepancies found

5. Loading strategies...
   ✓ Strategy loaded: vwap_mean_reversion
   ✓ Monitoring symbols: SPY

6. Starting Discord bot...
   ✓ Connected to Discord
   ✓ Notifications enabled

7. System ready!
   ✓ Heartbeat monitoring active
   ✓ Waiting for market open (9:30 AM ET)
```

### During Trading Hours

**Console Output:**
```
[09:30:00] Market OPEN
[09:31:00] [DATA] Received 1-min bar: SPY $450.50
[09:31:00] [STRATEGY] vwap_mean_reversion: No signal
[09:32:00] [DATA] Received 1-min bar: SPY $450.75
[09:32:00] [STRATEGY] vwap_mean_reversion: No signal
...
[10:15:00] [STRATEGY] vwap_mean_reversion: BUY signal (price < VWAP)
[10:15:01] [RISK] Pre-trade check: APPROVED
[10:15:01] [ORDER] Created: ORD_001 BUY 10 SPY @ MARKET
[10:15:02] [ORDER] Submitted to broker: broker_id=abc123
[10:15:03] [ORDER] FILLED: 10 SPY @ $450.50 (commission $0.10)
[10:15:03] [POSITION] Opened: SPY 10 shares @ $450.50
[10:15:03] [DISCORD] Notification sent to #paper-trading
```

**Discord Notifications:**
```
#paper-trading:
📊 Order Filled
Symbol: SPY
Side: BUY
Quantity: 10
Fill Price: $450.50
Commission: $0.10
Total Cost: $4,505.10
Strategy: vwap_mean_reversion
Time: 10:15:03 AM ET
```

```
#heartbeat (every 5 minutes):
💚 System Healthy
Uptime: 45 minutes
Open Positions: 1 (SPY: 10 shares)
Daily P&L: +$5.00
Orders Today: 1
Last Bar: SPY @ $450.75
```

### End of Day

```
[16:00:00] Market CLOSE
[16:00:01] Closing open positions...
[16:00:02] [ORDER] Created: ORD_002 SELL 10 SPY @ MARKET
[16:00:03] [ORDER] FILLED: 10 SPY @ $452.00
[16:00:03] [POSITION] Closed: SPY (P&L: +$15.00)
[16:00:05] Daily Summary:
   - Orders: 2
   - Fills: 2
   - Realized P&L: +$15.00
   - Unrealized P&L: $0.00
   - Daily P&L: +$15.00
[16:00:06] Shutting down gracefully...
[16:00:07] ✓ All components stopped
[16:00:07] ✓ Logs flushed
[16:00:07] ✓ Database persisted
[16:00:08] Goodbye!
```

---

## 🛑 HOW TO STOP THE SYSTEM

### Graceful Shutdown (Recommended)

**Press:** `Ctrl+C` in the terminal

**What happens:**
1. Stops accepting new signals
2. Completes pending orders
3. Optionally closes open positions
4. Saves all state to database
5. Flushes logs
6. Exits cleanly

### Emergency Stop (Kill Switch)

**If something goes wrong:**

```python
# In a separate Python terminal:
from core.execution.engine import TradingEngine
engine = TradingEngine.get_instance()
engine.activate_kill_switch(reason="Manual emergency stop")
```

**What kill switch does:**
1. Cancels ALL pending orders immediately
2. Closes ALL positions at market (immediately)
3. Disables all new trading
4. Logs critical event
5. Sends Discord alert

---

## 📈 MONITORING YOUR SYSTEM

### 1. Discord (Real-time)
- **#paper-trading** - Every trade
- **#heartbeat** - Health check every 5 min
- **#system-alerts** - Any errors or warnings

### 2. Log Files

**Location:** `C:\Users\Zacha\Desktop\MiniQuantDeskv2\logs\`

**Key logs:**
```
logs/
  trading/
    2026-01-20.log          # All trading activity today
  system/
    2026-01-20.log          # System events today
  heartbeats/
    2026-01-20.log          # Health checks
  transactions.jsonl        # Complete audit trail (all time)
```

**How to read transaction log:**
```bash
# View all fills today:
findstr "order_filled" logs\transactions.jsonl

# View all positions:
findstr "position_opened" logs\transactions.jsonl

# View all errors:
findstr "error" logs\system\2026-01-20.log
```

### 3. Position Store (Database)

**Check current positions:**
```python
import sqlite3
conn = sqlite3.connect('data/positions.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM positions WHERE is_open = 1")
for row in cursor.fetchall():
    print(row)
```

### 4. Daily Summary Email/Discord

At market close, you'll receive:
- Total orders today
- Total fills
- Realized P&L
- Unrealized P&L
- Win rate
- Largest winner/loser
- Risk limit status

---

## 🔍 VALIDATION CHECKLIST (Before Live Trading)

Run paper trading for **5+ consecutive sessions** and verify:

### Session 1 (Day 1)
- [ ] System starts without errors
- [ ] Broker connection successful
- [ ] Reconciliation runs (no discrepancies)
- [ ] Discord notifications working
- [ ] Strategy generates signals
- [ ] Risk gate approves/rejects correctly
- [ ] Orders submit to broker
- [ ] Fills received and tracked
- [ ] Positions updated correctly
- [ ] Logs written properly

### Session 2 (Day 2)
- [ ] System restarts successfully
- [ ] Reconciliation handles existing state
- [ ] Previous positions loaded correctly
- [ ] Continue from where left off
- [ ] Multiple fills handled correctly
- [ ] Stop loss triggers work
- [ ] Take profit triggers work

### Session 3 (Day 3)
- [ ] Run full trading day (9:30 AM - 4:00 PM ET)
- [ ] No memory leaks (check RAM usage)
- [ ] Performance acceptable (< 100ms per signal)
- [ ] No missed fills
- [ ] P&L calculations accurate
- [ ] End-of-day summary correct

### Session 4 (Day 4)
- [ ] Test error scenarios:
  - [ ] Bad market data (disconnect provider)
  - [ ] Broker timeout (slow connection)
  - [ ] Kill switch activation
  - [ ] Daily loss limit breach
  - [ ] Position size limit breach

### Session 5 (Day 5)
- [ ] Final validation day
- [ ] Review ALL logs for warnings
- [ ] Check all Discord notifications
- [ ] Verify no silent failures
- [ ] Confirm P&L matches broker
- [ ] System ready for decision

**Decision Point:**
- **All checks pass** → Ready for live trading with small capital
- **Any checks fail** → Debug, fix, restart validation

---

## 💰 TRANSITIONING TO LIVE TRADING (Future)

**DO NOT DO THIS YET** - Only after paper trading validation

When ready (after 5+ successful paper sessions):

### Step 1: Update Configuration

**Change in `_env`:**
```bash
# BEFORE (Paper):
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# AFTER (Live):
ALPACA_BASE_URL=https://api.alpaca.markets
ALPACA_API_KEY=your_live_api_key
ALPACA_SECRET_KEY=your_live_secret_key
```

**Change in `config/config.yaml`:**
```yaml
account:
  mode: live  # Changed from paper
  initial_capital: 1000.00  # START SMALL
```

### Step 2: Reduce Risk Limits (Be Conservative)

```yaml
risk:
  daily_loss_limit: 50.00      # 5% of $1000
  max_position_size: 200.00    # 20% of $1000
  max_orders_per_day: 10       # Limit activity
```

### Step 3: Start with ONE Symbol
```yaml
trading:
  symbols: [SPY]  # Just one until proven
```

### Step 4: Run First Live Session

**WATCH IT CLOSELY:**
- Monitor every trade
- Verify fills match expectations
- Check commissions
- Verify P&L calculations
- Ensure risk gates working

### Step 5: Gradual Scale-Up

```
Week 1: $1,000 capital, 1 symbol, 10 trades max/day
Week 2: $1,500 capital, 1 symbol, 20 trades max/day
Week 3: $2,000 capital, 2 symbols, 30 trades max/day
Month 2: $5,000 capital, 3 symbols, 50 trades max/day
```

**NEVER:**
- Jump from paper to large live capital
- Skip validation steps
- Ignore warnings or errors
- Trade without monitoring

---

## 🐛 TROUBLESHOOTING COMMON ISSUES

### Issue 1: "Cannot connect to Alpaca"

**Symptoms:**
```
ERROR: Failed to connect to broker
ConnectionError: 403 Forbidden
```

**Solution:**
1. Check API keys in `_env` are correct
2. Verify you're using PAPER keys, not live
3. Check `ALPACA_BASE_URL` is paper URL
4. Verify API keys are active in Alpaca dashboard
5. Check internet connection

### Issue 2: "No market data received"

**Symptoms:**
```
WARNING: No bars received for SPY
```

**Solution:**
1. Check Polygon API key valid
2. Verify subscription active (Polygon requires paid plan)
3. Check market is open (9:30-16:00 ET)
4. Try fallback provider (Finnhub, FMP)
5. Check internet connection

### Issue 3: "Discord notifications not working"

**Symptoms:**
- No messages in Discord channels

**Solution:**
1. Check `DISCORD_BOT_TOKEN` correct
2. Verify channel IDs correct (numeric, no quotes)
3. Ensure bot has permissions in channels
4. Check bot is online in Discord
5. Verify bot invited to server

### Issue 4: "Risk gate rejecting all trades"

**Symptoms:**
```
INFO: Order rejected by risk gate
Reason: Daily loss limit breached
```

**Solution:**
1. Check current daily P&L in database
2. Verify loss limit not actually breached
3. Check if new trading day (resets at midnight UTC)
4. Manually reset if needed:
   ```python
   from core.risk.limits import PersistentLimitsTracker
   tracker = PersistentLimitsTracker(...)
   tracker.reset_daily_limits()
   ```

### Issue 5: "Position not found after restart"

**Symptoms:**
```
WARNING: Reconciliation found position at broker not in local state
```

**Solution:**
1. **This is normal** - reconciler will fix it
2. Check reconciliation log (should say "Added to local")
3. Verify position now shows in position store
4. If persists, check broker dashboard manually

### Issue 6: "Memory usage growing"

**Symptoms:**
- RAM usage increases over time
- System slowing down

**Solution:**
1. Check cache size (default 10,000 bars)
2. Reduce cache size in config
3. Clear cache periodically
4. Check for log file rotation
5. Restart system daily as precaution

### Issue 7: "Strategy not generating signals"

**Symptoms:**
```
INFO: Strategy on_bar called
INFO: No signal generated
```

**Solution:**
1. **This is normal** - strategies don't signal every bar
2. Check strategy parameters (thresholds too strict?)
3. Verify market conditions meet entry criteria
4. Enable DEBUG logging to see strategy logic
5. Backtest strategy to verify it works at all

---

## 📚 USEFUL COMMANDS

### Check System Status
```bash
# View recent logs
tail -f logs/system/2026-01-20.log

# Check current positions
sqlite3 data/positions.db "SELECT * FROM positions WHERE is_open=1"

# Check daily P&L
sqlite3 data/limits.db "SELECT * FROM daily_limits ORDER BY date DESC LIMIT 1"
```

### Manual Reconciliation
```python
from core.state.reconciler import BrokerReconciler
reconciler = BrokerReconciler(...)
discrepancies = reconciler.reconcile_startup()
print(discrepancies)
```

### View Transaction History
```bash
# All fills
findstr "order_filled" logs\transactions.jsonl | python -m json.tool

# All positions closed
findstr "position_closed" logs\transactions.jsonl | python -m json.tool
```

### Reset Daily Limits (Emergency)
```python
from core.risk.limits import PersistentLimitsTracker
tracker = PersistentLimitsTracker(db_path="data/limits.db", ...)
tracker.reset_daily_limits()
print("Daily limits reset!")
```

---

## 📞 SUPPORT & DEBUGGING

### If System Crashes

**Data is safe:**
- Transaction log has all events
- Position database persisted
- Reconciliation will fix state on restart

**Steps:**
1. Don't panic
2. Read the error in console/logs
3. Check Discord #system-alerts
4. Restart system (reconciliation runs automatically)
5. Verify positions match broker
6. Resume trading

### If You Lose Money (Paper or Live)

**This is normal trading:**
- Not every trade wins
- Strategies have drawdowns
- Risk management prevents catastrophic loss

**Check:**
1. Was daily loss limit reached? (System stops if so)
2. Were risk rules followed? (Check logs)
3. Did strategy logic execute correctly? (Check logs)
4. Any system errors? (Check #system-alerts)

**Do NOT:**
- Immediately change strategy parameters
- Disable risk limits
- Bypass safety checks
- Trade more to "win it back"

---

## 🎯 FIRST SESSION CHECKLIST

**Before you start your first paper trading session:**

- [ ] Read this entire guide
- [ ] Read ARCHITECTURE_OVERVIEW.md (understand how it works)
- [ ] Configure `_env` with all API keys
- [ ] Configure `config/config.yaml` with conservative limits
- [ ] Create 7 Discord channels
- [ ] Test Alpaca connection
- [ ] Test Discord bot connection
- [ ] Understand how to stop system (Ctrl+C)
- [ ] Know where logs are (`logs/` directory)
- [ ] Understand risk limits (what will stop trading)
- [ ] Have browser ready for Alpaca dashboard
- [ ] Have Discord open for notifications
- [ ] Set aside 2+ hours to watch first session

**During first session:**

- [ ] Monitor console output continuously
- [ ] Watch Discord notifications
- [ ] Verify every order submission
- [ ] Verify every fill
- [ ] Check Alpaca dashboard matches system
- [ ] Note any warnings or errors
- [ ] Take notes on strategy behavior
- [ ] Verify risk gate rejections make sense

**After first session:**

- [ ] Review all logs
- [ ] Check final positions
- [ ] Verify P&L calculation
- [ ] Review any errors
- [ ] Plan improvements for next session

---

## 🚀 YOU'RE READY!

Your system is production-grade and ready for paper trading validation. 

**Start conservatively:**
1. One symbol (SPY)
2. Small position sizes
3. Strict risk limits
4. Close monitoring

**Scale gradually:**
1. Add more symbols
2. Increase position sizes
3. Relax limits (carefully)
4. Reduce monitoring

**Remember:**
- Paper trading is FREE - use it extensively
- Validate thoroughly before live trading
- Start live with minimal capital
- Scale up only after proven success

Good luck! 🎉

---

## QUICK REFERENCE

**Start Paper Trading:**
```bash
python entry_paper.py
```

**Stop Gracefully:**
```
Ctrl+C
```

**Emergency Stop:**
```python
engine.activate_kill_switch(reason="Emergency")
```

**Check Positions:**
```bash
sqlite3 data/positions.db "SELECT * FROM positions WHERE is_open=1"
```

**View Today's Logs:**
```bash
tail -f logs/system/2026-01-20.log
```

**Discord Alert Channels:**
- #paper-trading - All trades
- #system-alerts - Errors
- #heartbeat - Health checks
