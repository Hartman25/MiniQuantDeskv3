"""
Intraday drawdown monitoring and circuit breaker.

ARCHITECTURE:
- Track intraday peak equity
- Calculate peak-to-trough drawdown
- Halt trading on excessive drawdown
- Reset on new highs
- Daily statistics

DESIGN PRINCIPLE:
Prevent death spirals by stopping when down significantly.

EXAMPLE:
- Start day: $10,000
- Peak intraday: $10,500
- Current: $9,450 (10% from peak)
- → HALT TRADING

This prevents emotional revenge trading and cascading losses.

Based on professional risk management and behavioral finance principles.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# DRAWDOWN STATUS
# ============================================================================

class DrawdownStatus(Enum):
    """Drawdown status."""
    NORMAL = "NORMAL"  # Within normal range
    WARNING = "WARNING"  # Approaching limit
    HALT = "HALT"  # Exceeded limit - stop trading


@dataclass
class DrawdownEvent:
    """Drawdown event."""
    timestamp: datetime
    peak_equity: Decimal
    current_equity: Decimal
    drawdown_percent: Decimal
    drawdown_dollars: Decimal
    status: DrawdownStatus
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "peak_equity": str(self.peak_equity),
            "current_equity": str(self.current_equity),
            "drawdown_percent": str(self.drawdown_percent),
            "drawdown_dollars": str(self.drawdown_dollars),
            "status": self.status.value
        }


# ============================================================================
# INTRADAY DRAWDOWN MONITOR
# ============================================================================

class IntradayDrawdownMonitor:
    """
    Monitor intraday drawdown and halt on excessive losses.
    
    RESPONSIBILITIES:
    - Track intraday peak equity
    - Calculate drawdown from peak
    - Trigger warnings and halts
    - Reset daily
    - Generate statistics
    
    DRAWDOWN CALCULATION:
    drawdown_pct = (peak - current) / peak * 100
    
    THRESHOLDS:
    - Warning: 5% from peak
    - Halt: 10% from peak (configurable)
    
    USAGE:
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0")
        )
        
        # Update equity throughout day
        status = monitor.update_equity(Decimal("9500"))
        
        if status == DrawdownStatus.HALT:
            trading_engine.halt("Intraday drawdown limit exceeded")
            discord_notifier.send_risk_violation(...)
    """
    
    def __init__(
        self,
        starting_equity: Decimal,
        warning_threshold_percent: Decimal = Decimal("5.0"),
        halt_threshold_percent: Decimal = Decimal("10.0"),
        reset_on_new_high: bool = True
    ):
        """
        Initialize drawdown monitor.
        
        Args:
            starting_equity: Starting equity for the day
            warning_threshold_percent: % drawdown for warning
            halt_threshold_percent: % drawdown to halt trading
            reset_on_new_high: Reset thresholds on new equity highs
        """
        self.starting_equity = starting_equity
        self.warning_threshold = warning_threshold_percent
        self.halt_threshold = halt_threshold_percent
        self.reset_on_new_high = reset_on_new_high
        
        self.logger = get_logger(LogStream.RISK)
        
        # State
        self.peak_equity = starting_equity
        self.current_equity = starting_equity
        self.current_status = DrawdownStatus.NORMAL
        
        # History
        self.drawdown_history: List[DrawdownEvent] = []
        self.max_drawdown_today = Decimal("0")
        
        # Statistics
        self.warning_count = 0
        self.halt_count = 0
        self.last_update = datetime.now(timezone.utc)
        self.session_start = datetime.now(timezone.utc)
        
        self.logger.info("IntradayDrawdownMonitor initialized", extra={
            "starting_equity": str(starting_equity),
            "warning_threshold": str(warning_threshold_percent),
            "halt_threshold": str(halt_threshold_percent)
        })
    
    # ========================================================================
    # EQUITY UPDATES
    # ========================================================================
    
    def update_equity(
        self,
        current_equity: Decimal,
        timestamp: Optional[datetime] = None
    ) -> DrawdownStatus:
        """
        Update current equity and calculate drawdown.
        
        Args:
            current_equity: Current account equity
            timestamp: Update timestamp (defaults to now)
            
        Returns:
            DrawdownStatus (NORMAL/WARNING/HALT)
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        self.current_equity = current_equity
        self.last_update = timestamp
        
        # Update peak
        if current_equity > self.peak_equity:
            old_peak = self.peak_equity
            self.peak_equity = current_equity
            
            if self.reset_on_new_high and old_peak < current_equity:
                self.logger.info("New equity high - resetting drawdown", extra={
                    "old_peak": str(old_peak),
                    "new_peak": str(current_equity)
                })
        
        # Calculate drawdown
        drawdown_event = self._calculate_drawdown(timestamp)
        
        # Update max drawdown
        if drawdown_event.drawdown_percent > self.max_drawdown_today:
            self.max_drawdown_today = drawdown_event.drawdown_percent
        
        # Check status transition
        old_status = self.current_status
        new_status = drawdown_event.status
        
        if old_status != new_status:
            self._handle_status_change(old_status, new_status, drawdown_event)
        
        # Record event if significant
        if new_status != DrawdownStatus.NORMAL:
            self.drawdown_history.append(drawdown_event)
        
        self.current_status = new_status
        
        return new_status
    
    def _calculate_drawdown(self, timestamp: datetime) -> DrawdownEvent:
        """Calculate current drawdown."""
        # Drawdown from peak
        drawdown_dollars = self.peak_equity - self.current_equity
        
        if self.peak_equity > 0:
            drawdown_percent = (drawdown_dollars / self.peak_equity) * Decimal("100")
        else:
            drawdown_percent = Decimal("0")
        
        # Determine status
        if drawdown_percent >= self.halt_threshold:
            status = DrawdownStatus.HALT
        elif drawdown_percent >= self.warning_threshold:
            status = DrawdownStatus.WARNING
        else:
            status = DrawdownStatus.NORMAL
        
        return DrawdownEvent(
            timestamp=timestamp,
            peak_equity=self.peak_equity,
            current_equity=self.current_equity,
            drawdown_percent=drawdown_percent,
            drawdown_dollars=drawdown_dollars,
            status=status
        )
    
    def _handle_status_change(
        self,
        old_status: DrawdownStatus,
        new_status: DrawdownStatus,
        event: DrawdownEvent
    ):
        """Handle status change."""
        if new_status == DrawdownStatus.WARNING:
            self.warning_count += 1
            self.logger.warning(
                "⚠️ Drawdown WARNING",
                extra=event.to_dict()
            )
        
        elif new_status == DrawdownStatus.HALT:
            self.halt_count += 1
            self.logger.error(
                "🛑 Drawdown HALT - Stop trading",
                extra=event.to_dict()
            )
        
        elif new_status == DrawdownStatus.NORMAL and old_status != DrawdownStatus.NORMAL:
            self.logger.info(
                "✅ Drawdown recovered to NORMAL",
                extra=event.to_dict()
            )
    
    # ========================================================================
    # STATUS QUERIES
    # ========================================================================
    
    def get_current_drawdown(self) -> DrawdownEvent:
        """Get current drawdown event."""
        return self._calculate_drawdown(datetime.now(timezone.utc))
    
    def get_status(self) -> DrawdownStatus:
        """Get current drawdown status."""
        return self.current_status
    
    def is_trading_halted(self) -> bool:
        """Check if trading should be halted."""
        return self.current_status == DrawdownStatus.HALT
    
    def get_drawdown_percent(self) -> Decimal:
        """Get current drawdown percentage."""
        return self.get_current_drawdown().drawdown_percent
    
    def get_max_drawdown_today(self) -> Decimal:
        """Get maximum drawdown today."""
        return self.max_drawdown_today
    
    # ========================================================================
    # DAILY RESET
    # ========================================================================
    
    def reset_daily(self, new_starting_equity: Decimal):
        """
        Reset for new trading day.
        
        Args:
            new_starting_equity: Starting equity for new day
        """
        self.logger.info("Resetting drawdown monitor for new day", extra={
            "old_starting": str(self.starting_equity),
            "new_starting": str(new_starting_equity),
            "max_drawdown_yesterday": str(self.max_drawdown_today),
            "warning_count": self.warning_count,
            "halt_count": self.halt_count
        })
        
        # Reset state
        self.starting_equity = new_starting_equity
        self.peak_equity = new_starting_equity
        self.current_equity = new_starting_equity
        self.current_status = DrawdownStatus.NORMAL
        
        # Clear history
        self.drawdown_history.clear()
        self.max_drawdown_today = Decimal("0")
        
        # Reset counters
        self.warning_count = 0
        self.halt_count = 0
        self.session_start = datetime.now(timezone.utc)
    
    # ========================================================================
    # MANUAL OVERRIDES
    # ========================================================================
    
    def reset_halt(self):
        """
        Manually reset halt status (use with caution).
        
        WARNING: Only use this if you've analyzed the situation
        and determined trading can safely resume.
        """
        self.logger.warning("Manually resetting halt status", extra={
            "current_drawdown": str(self.get_drawdown_percent()),
            "current_equity": str(self.current_equity),
            "peak_equity": str(self.peak_equity)
        })
        
        # Reset to WARNING (not NORMAL) as safeguard
        if self.get_drawdown_percent() >= self.warning_threshold:
            self.current_status = DrawdownStatus.WARNING
        else:
            self.current_status = DrawdownStatus.NORMAL
    
    def force_halt(self, reason: str):
        """
        Manually force halt (emergency use).
        
        Args:
            reason: Reason for manual halt
        """
        self.logger.error(f"Manual halt triggered: {reason}")
        self.current_status = DrawdownStatus.HALT
        self.halt_count += 1
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_statistics(self) -> dict:
        """Get drawdown statistics for the session."""
        session_duration = (datetime.now(timezone.utc) - self.session_start).total_seconds() / 3600
        
        return {
            "session_duration_hours": round(session_duration, 2),
            "starting_equity": str(self.starting_equity),
            "peak_equity": str(self.peak_equity),
            "current_equity": str(self.current_equity),
            "current_drawdown_pct": str(self.get_drawdown_percent()),
            "max_drawdown_pct": str(self.max_drawdown_today),
            "warning_count": self.warning_count,
            "halt_count": self.halt_count,
            "current_status": self.current_status.value,
            "drawdown_events": len(self.drawdown_history)
        }
    
    def get_recent_events(self, count: int = 10) -> List[DrawdownEvent]:
        """Get recent drawdown events."""
        return self.drawdown_history[-count:]
