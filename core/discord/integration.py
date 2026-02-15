"""
Discord integration manager - connects notifier to system events.

FEATURES:
- Event bus subscription
- Automatic notifications
- Daily summary scheduler
- Error monitoring
- Position tracking
"""

from typing import Optional, Dict
from decimal import Decimal
from datetime import datetime, time as datetime_time, timezone
import threading
import schedule
import time

from core.logging import get_logger, LogStream
from core.events import OrderEventBus
from core.state import OrderStatus, PositionStore
from core.state.order_machine import OrderStateChangedEvent
from core.discord.notifier import DiscordNotifier


# ============================================================================
# DISCORD INTEGRATION MANAGER
# ============================================================================

class DiscordIntegrationManager:
    """
    Integrates Discord notifications with system events.
    
    FEATURES:
    - Auto-subscribe to event bus
    - Trade notifications
    - Daily summaries
    - Error alerts
    - Position updates
    
    USAGE:
        manager = DiscordIntegrationManager(
            notifier=notifier,
            event_bus=event_bus,
            position_store=position_store
        )
        
        manager.start()
        manager.send_startup(mode="PAPER", account_value=Decimal("100000"))
    """
    
    def __init__(
        self,
        notifier: DiscordNotifier,
        event_bus: OrderEventBus,
        position_store: PositionStore
    ):
        """Initialize integration manager."""
        self.notifier = notifier
        self.event_bus = event_bus
        self.position_store = position_store
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Tracking
        self._daily_trades = 0
        self._daily_pnl = Decimal("0")
        
        # Scheduler thread
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_running = False
        
        # Subscribe to events
        self._subscribe_to_events()
        
        self.logger.info("DiscordIntegrationManager initialized")
    
    def _subscribe_to_events(self):
        """Subscribe to event bus."""
        # Order state changes
        self.event_bus.subscribe(
            OrderStateChangedEvent,
            self._handle_order_state_change
        )
    
    def _handle_order_state_change(self, event: OrderStateChangedEvent):
        """Handle order state change event."""
        try:
            # Order submitted
            if event.to_state == OrderStatus.SUBMITTED:
                # Get order metadata from event
                metadata = event.metadata or {}
                
                self.notifier.send_trade_submitted(
                    order_id=event.order_id,
                    symbol=metadata.get("symbol", "UNKNOWN"),
                    quantity=metadata.get("quantity", Decimal("0")),
                    side=metadata.get("side", "UNKNOWN"),
                    strategy=metadata.get("strategy", "UNKNOWN")
                )
            
            # Order filled
            elif event.to_state == OrderStatus.FILLED:
                metadata = event.metadata or {}
                
                self.notifier.send_trade_filled(
                    order_id=event.order_id,
                    symbol=metadata.get("symbol", "UNKNOWN"),
                    quantity=event.filled_qty or Decimal("0"),
                    price=event.fill_price or Decimal("0"),
                    side=metadata.get("side", "UNKNOWN"),
                    strategy=metadata.get("strategy", "UNKNOWN")
                )
                
                # Track for daily summary
                self._daily_trades += 1
            
            # Order rejected/cancelled
            elif event.to_state in [OrderStatus.REJECTED, OrderStatus.CANCELLED]:
                metadata = event.metadata or {}
                
                self.notifier.send_trade_rejected(
                    order_id=event.order_id,
                    symbol=metadata.get("symbol", "UNKNOWN"),
                    reason=event.reason or "Unknown"
                )
                
        except Exception as e:
            self.logger.error("Error handling order event", extra={
                "error": str(e)
            }, exc_info=True)
    
    def send_startup(self, mode: str, account_value: Optional[Decimal] = None):
        """Send startup notification."""
        self.notifier.send_startup(mode, account_value)
    
    def send_shutdown(self, reason: str = "Normal shutdown"):
        """Send shutdown notification."""
        self.notifier.send_shutdown(reason)
    
    def send_alert(self, title: str, message: str, severity: str = "WARNING"):
        """Send alert."""
        self.notifier.send_alert(title, message, severity)
    
    def send_risk_violation(self, violation_type: str, details: str):
        """Send risk violation alert."""
        self.notifier.send_risk_violation(violation_type, details)
    
    def send_error(self, error_type: str, message: str, traceback: Optional[str] = None):
        """Send error notification."""
        self.notifier.send_error(error_type, message, traceback)
    
    def start_daily_summary(self, summary_time: str = "16:00"):
        """
        Start daily summary scheduler.
        
        Args:
            summary_time: Time to send summary (HH:MM format)
        """
        if self._scheduler_running:
            return
        
        # Schedule daily summary
        schedule.every().day.at(summary_time).do(self._send_daily_summary)
        
        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(
            target=self._run_scheduler,
            daemon=True
        )
        self._scheduler_thread.start()
        
        self.logger.info(f"Daily summary scheduled at {summary_time}")
    
    def stop_daily_summary(self):
        """Stop daily summary scheduler."""
        self._scheduler_running = False
        
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)
        
        schedule.clear()
        self.logger.info("Daily summary stopped")
    
    def _run_scheduler(self):
        """Run scheduler loop."""
        while self._scheduler_running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    def _send_daily_summary(self):
        """Send daily summary (called by scheduler)."""
        try:
            # Get positions
            positions = self.position_store.get_all()
            
            # Calculate total P&L
            total_pnl = sum(
                (pos.unrealized_pnl or Decimal("0")) for pos in positions
            )
            
            # Calculate win rate (simplified)
            # In production, track wins/losses properly
            win_rate = 0.0
            if self._daily_trades > 0:
                win_rate = 0.5  # Placeholder
            
            # Get account value (would get from broker in production)
            account_value = Decimal("0")
            
            self.notifier.send_daily_summary(
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                trades_count=self._daily_trades,
                pnl=total_pnl,
                win_rate=win_rate,
                positions_count=len(positions),
                account_value=account_value
            )
            
            # Reset daily counters
            self._daily_trades = 0
            self._daily_pnl = Decimal("0")
            
        except Exception as e:
            self.logger.error("Error sending daily summary", extra={
                "error": str(e)
            }, exc_info=True)


# ============================================================================
# ERROR MONITOR
# ============================================================================

class DiscordErrorMonitor:
    """
    Monitors logs and sends critical errors to Discord.
    
    USAGE:
        monitor = DiscordErrorMonitor(notifier)
        monitor.monitor_error(Exception("Something broke"), "ExecutionEngine")
    """
    
    def __init__(self, notifier: DiscordNotifier):
        """Initialize error monitor."""
        self.notifier = notifier
        self.logger = get_logger(LogStream.SYSTEM)
    
    def monitor_error(self, exception: Exception, component: str):
        """Monitor and report error."""
        import traceback
        
        tb = traceback.format_exc()
        
        self.notifier.send_error(
            error_type=component,
            message=str(exception),
            traceback=tb
        )
        
        self.logger.error(f"Error in {component}", extra={
            "error": str(exception)
        }, exc_info=True)
