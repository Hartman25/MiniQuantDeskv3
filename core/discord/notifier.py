"""
Discord notifier - sends alerts and notifications.

ARCHITECTURE:
- Webhook-based notifications (no bot token needed for sending)
- Event subscriptions via OrderEventBus
- Rich embeds with formatting
- Rate limiting protection
- Retry logic
- Alert prioritization

CHANNELS:
- System: Start/stop, errors, health
- Trading: Signals, orders, fills
- Risk: Limit violations, circuit breakers
- Daily: EOD summaries, performance

Based on production alerting patterns.
"""

from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime, UTC, timezone
from enum import Enum
import json
import time
import threading
from collections import deque

import requests

from core.logging import get_logger, LogStream
from core.time.clock import local_time_str, utc_now


# ============================================================================
# NOTIFICATION TYPES
# ============================================================================

class NotificationPriority(Enum):
    """Notification priority levels."""
    LOW = "LOW"          # Info only
    MEDIUM = "MEDIUM"    # Important updates
    HIGH = "HIGH"        # Warnings
    CRITICAL = "CRITICAL"  # Errors, kill switches


class NotificationChannel(Enum):
    """Notification channels."""
    SYSTEM = "SYSTEM"      # System events
    TRADING = "TRADING"    # Trade execution
    RISK = "RISK"          # Risk alerts
    DAILY = "DAILY"        # Daily summaries


# ============================================================================
# DISCORD NOTIFIER
# ============================================================================

class DiscordNotifier:
    """
    Discord webhook notifier.
    
    FEATURES:
    - Multiple webhook channels
    - Rich embeds
    - Priority-based formatting
    - Rate limiting (5 msgs/5sec per webhook)
    - Retry on failure
    - Async send via thread pool
    
    USAGE:
        notifier = DiscordNotifier(webhooks={
            NotificationChannel.SYSTEM: "https://discord.com/api/webhooks/...",
            NotificationChannel.TRADING: "https://discord.com/api/webhooks/..."
        })
        
        notifier.send_system_start(version="2.0")
        
        notifier.send_trade_execution(
            symbol="SPY",
            side="BUY",
            quantity=10,
            price=Decimal("600")
        )
    """
    
    def __init__(self, webhooks: Dict[NotificationChannel, str]):
        """
        Initialize notifier.
        
        Args:
            webhooks: Dict mapping channel to webhook URL
        """
        self.webhooks = webhooks
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Rate limiting: 5 messages per 5 seconds per webhook
        self._rate_limits: Dict[NotificationChannel, deque] = {
            channel: deque(maxlen=5)
            for channel in NotificationChannel
        }
        self._rate_limit_lock = threading.Lock()
        
        # Thread pool for async sends
        self._send_queue = deque()
        self._send_thread = threading.Thread(target=self._send_worker, daemon=True)
        self._running = False
        
        self.logger.info("DiscordNotifier initialized", extra={
            "channels": [ch.value for ch in webhooks.keys()]
        })
    
    def start(self):
        """Start async send worker."""
        if self._running:
            return
        
        self._running = True
        self._send_thread.start()
        self.logger.info("Discord notifier started")
    
    def stop(self):
        """Stop async send worker."""
        self._running = False
        if self._send_thread.is_alive():
            self._send_thread.join(timeout=5)
        self.logger.info("Discord notifier stopped")
    
    # ========================================================================
    # SYSTEM NOTIFICATIONS
    # ========================================================================
    
    def send_system_start(self, version: str, mode: str = "PAPER"):
        """Send system start notification."""
        self._send(
            channel=NotificationChannel.SYSTEM,
            priority=NotificationPriority.HIGH,
            title="🚀 System Started",
            description=f"MiniQuantDesk v{version} is now running",
            fields=[
                {"name": "Mode", "value": mode, "inline": True},
                {"name": "Started", "value": local_time_str("%Y-%m-%d %H:%M:%S %Z"), "inline": True}
            ],
            color=0x00FF00  # Green
        )
    
    def send_system_stop(self, reason: str = "Manual shutdown"):
        """Send system stop notification."""
        self._send(
            channel=NotificationChannel.SYSTEM,
            priority=NotificationPriority.HIGH,
            title="🛑 System Stopped",
            description=reason,
            fields=[
                {"name": "Stopped", "value": local_time_str("%Y-%m-%d %H:%M:%S %Z"), "inline": True}
            ],
            color=0xFF0000  # Red
        )
    
    def send_error(self, error: str, details: Optional[str] = None):
        """Send error alert."""
        fields = [
            {"name": "Error", "value": error[:1024], "inline": False}
        ]
        
        if details:
            fields.append({"name": "Details", "value": details[:1024], "inline": False})
        
        self._send(
            channel=NotificationChannel.SYSTEM,
            priority=NotificationPriority.CRITICAL,
            title="❌ Error Alert",
            description="System error detected",
            fields=fields,
            color=0xFF0000  # Red
        )
    
    # ========================================================================
    # TRADING NOTIFICATIONS
    # ========================================================================
    
    def send_signal_generated(
        self,
        symbol: str,
        signal_type: str,
        strategy: str,
        confidence: Decimal
    ):
        """Send trading signal notification."""
        emoji = "📈" if signal_type == "LONG" else "📉" if signal_type == "SHORT" else "⏸️"
        
        self._send(
            channel=NotificationChannel.TRADING,
            priority=NotificationPriority.MEDIUM,
            title=f"{emoji} Signal: {symbol}",
            description=f"{signal_type} signal generated",
            fields=[
                {"name": "Strategy", "value": strategy, "inline": True},
                {"name": "Confidence", "value": f"{float(confidence)*100:.1f}%", "inline": True},
                {"name": "Time", "value": local_time_str("%H:%M:%S %Z"), "inline": True}
            ],
            color=0x00FF00 if signal_type == "LONG" else 0xFF0000
        )
    
    def send_order_submitted(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_id: str
    ):
        """Send order submitted notification."""
        emoji = "🔵" if side == "BUY" else "🔴"
        
        self._send(
            channel=NotificationChannel.TRADING,
            priority=NotificationPriority.MEDIUM,
            title=f"{emoji} Order Submitted: {symbol}",
            description=f"{side} {quantity} shares",
            fields=[
                {"name": "Order ID", "value": order_id[:20], "inline": True},
                {"name": "Time", "value": local_time_str("%H:%M:%S %Z"), "inline": True}
            ],
            color=0x0000FF
        )
    
    def send_trade_execution(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        order_id: str
    ):
        """Send trade execution notification."""
        emoji = "✅" if side == "BUY" else "❎"
        value = quantity * price
        
        self._send(
            channel=NotificationChannel.TRADING,
            priority=NotificationPriority.HIGH,
            title=f"{emoji} Trade Executed: {symbol}",
            description=f"{side} {quantity} @ ${price}",
            fields=[
                {"name": "Value", "value": f"${value:,.2f}", "inline": True},
                {"name": "Order ID", "value": order_id[:20], "inline": True},
                {"name": "Time", "value": local_time_str("%H:%M:%S %Z"), "inline": True}
            ],
            color=0x00FF00
        )
    
    def send_position_closed(
        self,
        symbol: str,
        quantity: Decimal,
        entry_price: Decimal,
        exit_price: Decimal,
        pnl: Decimal,
        pnl_pct: Decimal
    ):
        """Send position closed notification."""
        emoji = "💰" if pnl > 0 else "💸"
        
        self._send(
            channel=NotificationChannel.TRADING,
            priority=NotificationPriority.HIGH,
            title=f"{emoji} Position Closed: {symbol}",
            description=f"P&L: ${pnl:,.2f} ({pnl_pct:+.2%})",
            fields=[
                {"name": "Quantity", "value": str(quantity), "inline": True},
                {"name": "Entry", "value": f"${entry_price}", "inline": True},
                {"name": "Exit", "value": f"${exit_price}", "inline": True}
            ],
            color=0x00FF00 if pnl > 0 else 0xFF0000
        )
    
    # ========================================================================
    # RISK NOTIFICATIONS
    # ========================================================================
    
    def send_risk_violation(self, violation: str, details: str):
        """Send risk violation alert."""
        self._send(
            channel=NotificationChannel.RISK,
            priority=NotificationPriority.CRITICAL,
            title="⚠️ Risk Violation",
            description=violation,
            fields=[
                {"name": "Details", "value": details[:1024], "inline": False}
            ],
            color=0xFFA500  # Orange
        )
    
    def send_drawdown_alert(self, current_dd: Decimal, max_dd: Decimal):
        """Send drawdown alert."""
        self._send(
            channel=NotificationChannel.RISK,
            priority=NotificationPriority.CRITICAL,
            title="🚨 Drawdown Alert",
            description=f"Current drawdown: {current_dd:.2%}",
            fields=[
                {"name": "Max Allowed", "value": f"{max_dd:.2%}", "inline": True},
                {"name": "Status", "value": "APPROACHING LIMIT", "inline": True}
            ],
            color=0xFF0000
        )
    
    def send_position_drift(self, symbol: str, local_qty: Decimal, broker_qty: Decimal):
        """Send position drift alert."""
        self._send(
            channel=NotificationChannel.RISK,
            priority=NotificationPriority.HIGH,
            title="⚠️ Position Drift Detected",
            description=f"{symbol} quantities do not match",
            fields=[
                {"name": "Local", "value": str(local_qty), "inline": True},
                {"name": "Broker", "value": str(broker_qty), "inline": True},
                {"name": "Drift", "value": str(broker_qty - local_qty), "inline": True}
            ],
            color=0xFFA500
        )
    
    # ========================================================================
    # DAILY SUMMARY
    # ========================================================================
    
    def send_daily_summary(self, summary: Dict):
        """Send end-of-day summary."""
        self._send(
            channel=NotificationChannel.DAILY,
            priority=NotificationPriority.MEDIUM,
            title="📊 Daily Summary",
            description=f"Trading Day: {summary.get('date', 'N/A')}",
            fields=[
                {"name": "Trades", "value": str(summary.get('trades', 0)), "inline": True},
                {"name": "P&L", "value": f"${summary.get('pnl', 0):,.2f}", "inline": True},
                {"name": "Win Rate", "value": f"{summary.get('win_rate', 0):.1%}", "inline": True},
                {"name": "Largest Win", "value": f"${summary.get('largest_win', 0):,.2f}", "inline": True},
                {"name": "Largest Loss", "value": f"${summary.get('largest_loss', 0):,.2f}", "inline": True},
                {"name": "Sharpe", "value": f"{summary.get('sharpe', 0):.2f}", "inline": True}
            ],
            color=0x0099FF  # Blue
        )
    
    # ========================================================================
    # INTERNAL SEND LOGIC
    # ========================================================================
    
    def _send(
        self,
        channel: NotificationChannel,
        priority: NotificationPriority,
        title: str,
        description: str,
        fields: List[Dict],
        color: int
    ):
        """Queue notification for sending."""
        webhook_url = self.webhooks.get(channel)
        if not webhook_url:
            self.logger.warning(f"No webhook for channel: {channel.value}")
            return
        
        # Check rate limit
        if not self._check_rate_limit(channel):
            self.logger.warning(f"Rate limit hit for channel: {channel.value}")
            return
        
        # Build embed
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            # Discord expects an ISO timestamp. We always send UTC here.
            "timestamp": utc_now().isoformat(),
            "footer": {"text": f"Priority: {priority.value}"}
        }
        
        payload = {"embeds": [embed]}
        
        # Queue for async send
        self._send_queue.append((webhook_url, payload))
    
    def _check_rate_limit(self, channel: NotificationChannel) -> bool:
        """Check if send is within rate limit."""
        with self._rate_limit_lock:
            now = time.time()
            timestamps = self._rate_limits[channel]
            
            # Remove timestamps older than 5 seconds
            while timestamps and now - timestamps[0] > 5.0:
                timestamps.popleft()
            
            # Check if we can send
            if len(timestamps) >= 5:
                return False
            
            # Add current timestamp
            timestamps.append(now)
            return True
    
    def _send_worker(self):
        """Worker thread for sending notifications."""
        while self._running:
            try:
                if self._send_queue:
                    webhook_url, payload = self._send_queue.popleft()
                    self._send_webhook(webhook_url, payload)
                else:
                    time.sleep(0.1)
            except Exception as e:
                self.logger.error("Send worker error", extra={"error": str(e)}, exc_info=True)
    
    def _send_webhook(self, webhook_url: str, payload: Dict, retries: int = 3):
        """Send webhook with retry."""
        for attempt in range(retries):
            try:
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=10
                )
                
                if response.status_code == 204:
                    return  # Success
                
                elif response.status_code == 429:
                    # Rate limited
                    retry_after = int(response.headers.get('Retry-After', 5))
                    self.logger.warning(f"Discord rate limit, retry after {retry_after}s")
                    time.sleep(retry_after)
                
                else:
                    self.logger.error(f"Discord webhook error: {response.status_code}")
                    
            except Exception as e:
                self.logger.error(f"Webhook send failed (attempt {attempt+1})", extra={
                    "error": str(e)
                }, exc_info=True)
                
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
