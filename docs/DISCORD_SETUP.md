# Discord Integration Setup Guide

**MiniQuantDesk v2 - Discord Remote Control & Notifications**

---

## Overview

The Discord integration provides:
- **Real-time notifications** via webhooks (trade execution, system alerts, risk warnings)
- **Remote control** via slash commands (/status, /start, /stop, /kill)
- **Daily summaries** (EOD performance reports)
- **Emergency shutdown** from your phone

---

## Part 1: Discord Bot Setup

### Step 1: Create Discord Application

1. Go to https://discord.com/developers/applications
2. Click **"New Application"**
3. Name it **"MiniQuantDesk Trading Bot"**
4. Click **"Create"**

### Step 2: Configure Bot

1. Go to **"Bot"** tab (left sidebar)
2. Click **"Add Bot"** → **"Yes, do it!"**
3. Under **"Privileged Gateway Intents"**:
   - Enable **MESSAGE CONTENT INTENT**
4. Copy the **Bot Token** (you'll need this)

### Step 3: Set Bot Permissions

1. Go to **"OAuth2"** → **"URL Generator"**
2. Select scopes:
   - `bot`
   - `applications.commands`
3. Select permissions:
   - Send Messages
   - Use Slash Commands
   - Embed Links
4. Copy the generated URL

### Step 4: Invite Bot to Server

1. Paste the URL from Step 3 into browser
2. Select your Discord server
3. Click **"Authorize"**
4. Complete CAPTCHA

---

## Part 2: Webhook Setup

You need 4 separate webhooks (one per channel).

### Recommended Channel Structure:
```
📊 Trading Server
  ├── 🖥️ system-alerts      (System start/stop, errors)
  ├── 💹 trading-updates    (Orders, fills, signals)
  ├── ⚠️ risk-alerts        (Violations, drawdowns)
  └── 📈 daily-reports      (EOD summaries)
```

### Create Webhooks:

For **each channel**:

1. Right-click the channel → **"Edit Channel"**
2. Go to **"Integrations"** → **"Webhooks"**
3. Click **"New Webhook"**
4. Name it (e.g., "MiniQuantDesk System")
5. Click **"Copy Webhook URL"**
6. Save the URL (you'll need all 4)

---

## Part 3: Get Your Discord User ID

1. Enable Developer Mode:
   - Settings → Advanced → **Developer Mode** (turn on)
2. Right-click your username → **"Copy User ID"**
3. Save this ID (authorizes you to use commands)

---

## Part 4: Configuration

### Add to `.env` file:

Create/edit `config/.env.local`:

```bash
# Discord Bot
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE

# Discord Webhooks
DISCORD_WEBHOOK_SYSTEM=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_TRADING=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_RISK=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_DAILY=https://discord.com/api/webhooks/...

# Authorized Users (comma-separated)
DISCORD_AUTHORIZED_USER_IDS=123456789,987654321
```

### Update `config.yaml`:

```yaml
discord:
  enabled: true
  notifications:
    system: true
    trading: true
    risk: true
    daily: true
  commands:
    enabled: true
    emergency_shutdown: true
```

---

## Part 5: Integration Code

### Minimal Integration Example:

```python
from core.discord import (
    DiscordNotifier,
    TradingBot,
    DiscordEventBridge,
    NotificationChannel
)
from core.events import OrderEventBus

# Load webhooks from config
webhooks = {
    NotificationChannel.SYSTEM: config.discord.webhook_system,
    NotificationChannel.TRADING: config.discord.webhook_trading,
    NotificationChannel.RISK: config.discord.webhook_risk,
    NotificationChannel.DAILY: config.discord.webhook_daily,
}

# Initialize notifier
notifier = DiscordNotifier(webhooks=webhooks)
notifier.start()

# Send startup notification
notifier.send_system_start(version="2.0", mode="PAPER")

# Connect to event bus
event_bus = OrderEventBus()
bridge = DiscordEventBridge(event_bus=event_bus, notifier=notifier)
bridge.start()

# Initialize bot (for commands)
bot = TradingBot(
    token=config.discord.bot_token,
    authorized_users=[123456789],  # Your user ID
    system_controller=your_system_controller
)
bot.start_bot()
```

---

## Part 6: Test Your Setup

### Test Webhooks:

```python
from core.discord import DiscordNotifier, NotificationChannel
from decimal import Decimal

notifier = DiscordNotifier(webhooks={
    NotificationChannel.SYSTEM: "YOUR_WEBHOOK_URL"
})
notifier.start()

# Test notification
notifier.send_system_start(version="2.0", mode="TEST")
```

Check your Discord channel - you should see a message!

### Test Bot Commands:

1. Go to your Discord server
2. Type `/` in any channel
3. You should see MiniQuantDesk commands:
   - `/status`
   - `/positions`
   - `/pnl`
   - `/start`
   - `/stop`
   - `/kill`

---

## Slash Commands Reference

### `/status`
Get system health and current state.

**Returns:**
- Status (RUNNING/STOPPED)
- Mode (PAPER/LIVE)
- Uptime
- Active positions
- Today's P&L

### `/positions`
View all active positions.

**Returns:**
- Symbol, quantity, entry price
- Current P&L per position
- Limited to 10 positions per message

### `/pnl`
Get today's P&L breakdown.

**Returns:**
- Total P&L
- Realized vs unrealized
- Number of trades
- Win rate

### `/start`
Start trading.

**⚠️ Authorization Required**

### `/stop`
Stop trading (graceful shutdown).

**⚠️ Authorization Required**

### `/kill`
Emergency shutdown (immediate).

**⚠️ Authorization Required**  
**🚨 Use only in emergencies!**

---

## Notification Types

### System Notifications (🖥️ system-alerts)
- System started/stopped
- Errors and exceptions
- Health check failures

### Trading Notifications (💹 trading-updates)
- Signals generated
- Orders submitted
- Trades executed
- Positions closed

### Risk Notifications (⚠️ risk-alerts)
- Risk violations
- Drawdown warnings
- Position drift detected
- Circuit breaker triggered

### Daily Reports (📈 daily-reports)
- End-of-day summary
- Performance metrics
- Win/loss breakdown

---

## Security Best Practices

1. **Keep bot token secret** - Never commit to git
2. **Limit authorized users** - Only add trusted user IDs
3. **Use separate channels** - Don't mix critical alerts with noise
4. **Test in paper mode first** - Verify everything works before live
5. **Enable 2FA** - On Discord account
6. **Monitor webhook usage** - Discord has rate limits

---

## Rate Limits

Discord has the following limits:

- **Webhooks:** 5 requests per 5 seconds per webhook
- **Slash commands:** 50 requests per second (bot-wide)

**MiniQuantDesk handles this automatically:**
- Rate limiting protection
- Exponential backoff
- Retry logic

---

## Troubleshooting

### Bot doesn't respond to commands:
1. Check bot is online in server member list
2. Verify bot has proper permissions
3. Re-sync commands: Delete and re-invite bot
4. Check bot token is correct

### Webhooks not working:
1. Verify webhook URL is correct
2. Check channel permissions
3. Test webhook with curl:
```bash
curl -X POST "WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"content":"Test message"}'
```

### "Unauthorized" errors:
1. Verify your user ID is in DISCORD_AUTHORIZED_USER_IDS
2. Make sure you copied the full ID (no spaces)
3. Check .env file is loaded correctly

### Rate limit errors:
- Too many notifications too fast
- System automatically throttles
- Consider reducing notification frequency

---

## Advanced: Custom Notifications

```python
# Custom trade notification
notifier.send_trade_execution(
    symbol="SPY",
    side="BUY",
    quantity=Decimal("100"),
    price=Decimal("600.50"),
    order_id="ORD_123456"
)

# Custom risk alert
notifier.send_risk_violation(
    violation="Position size exceeded",
    details="SPY position: $75,000 (max: $50,000)"
)

# Custom daily summary
notifier.send_daily_summary({
    "date": "2026-01-19",
    "trades": 10,
    "pnl": 523.75,
    "win_rate": 0.7,
    "largest_win": 150.00,
    "largest_loss": -75.50,
    "sharpe": 1.85
})
```

---

## Mobile Usage

**Control your bot from anywhere:**

1. Install Discord mobile app
2. Join your trading server
3. Use slash commands:
   - Check status while away from computer
   - Stop trading if needed
   - Emergency shutdown button

**Best Practice:**
- Set up Discord notifications on phone
- Enable critical alert sounds
- Test commands before going live

---

## Production Checklist

Before going live:

- [ ] Bot token configured and tested
- [ ] All 4 webhooks working
- [ ] User ID authorization tested
- [ ] Slash commands responding
- [ ] Emergency shutdown tested
- [ ] Rate limits verified
- [ ] Notification formatting looks good
- [ ] Mobile Discord app installed
- [ ] Critical alerts have sound enabled
- [ ] Paper trading notifications working

---

## Example: Full Day Flow

**Morning (Pre-Market):**
```
🖥️ System Alert
🚀 System Started
MiniQuantDesk v2.0 is now running
Mode: PAPER | Started: 05:30:00 HST
```

**During Market:**
```
💹 Trading Update
📈 Signal: SPY
LONG signal generated
Strategy: VWAP_MeanReversion | Confidence: 85%

💹 Trading Update
🔵 Order Submitted: SPY
BUY 100 shares
Order ID: ORD_1234

💹 Trading Update
✅ Trade Executed: SPY
BUY 100 @ $600.50
Value: $60,050.00 | Order ID: ORD_1234
```

**End of Day:**
```
📈 Daily Summary
Trading Day: 2026-01-19
Trades: 5 | P&L: $352.50 | Win Rate: 80.0%
Largest Win: $150.00 | Largest Loss: -$45.50
Sharpe: 1.75
```

---

**You now have full remote control and monitoring! 📱💹**
