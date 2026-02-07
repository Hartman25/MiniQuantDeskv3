"""
Strategy Performance Tracker with auto-cutoff.

ARCHITECTURE:
- Track per-strategy performance metrics
- Auto-disable underperforming strategies
- Monitor recent performance windows
- Calculate Sharpe ratio per strategy
- Provide strategy rankings

DESIGN PRINCIPLE:
Kill losers, scale winners.

AUTO-CUTOFF RULES:
- Sharpe ratio < threshold → Disable
- Consecutive losing days → Disable
- Win rate < threshold → Disable
- Drawdown > threshold → Disable

Based on institutional portfolio manager evaluation systems.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from collections import deque
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# STRATEGY STATUS
# ============================================================================

class StrategyStatus(Enum):
    """Strategy operational status."""
    ACTIVE = "active"
    DISABLED_PERFORMANCE = "disabled_performance"
    DISABLED_RISK = "disabled_risk"
    DISABLED_MANUAL = "disabled_manual"


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    timestamp: datetime
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    pnl_percent: Decimal
    duration_seconds: int


@dataclass
class StrategyMetrics:
    """Performance metrics for a strategy."""
    strategy_id: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    total_pnl: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    consecutive_losses: int
    status: StrategyStatus
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "strategy_id": self.strategy_id,
            "total_trades": self.total_trades,
            "win_rate": float(self.win_rate),
            "total_pnl": float(self.total_pnl),
            "sharpe_ratio": float(self.sharpe_ratio),
            "max_drawdown": float(self.max_drawdown),
            "status": self.status.value
        }


# ============================================================================
# STRATEGY PERFORMANCE TRACKER
# ============================================================================

class StrategyPerformanceTracker:
    """
    Track strategy performance and auto-disable losers.
    
    RESPONSIBILITIES:
    - Record all trades per strategy
    - Calculate performance metrics
    - Monitor recent performance windows
    - Auto-disable underperformers
    - Provide strategy rankings
    
    AUTO-CUTOFF THRESHOLDS:
    - Min Sharpe ratio: Configurable (default: 0.5)
    - Max consecutive losses: Configurable (default: 5)
    - Min win rate: Configurable (default: 35%)
    - Max drawdown: Configurable (default: 15%)
    
    USAGE:
        tracker = StrategyPerformanceTracker(
            min_sharpe_ratio=Decimal("0.5"),
            max_consecutive_losses=5,
            min_win_rate_percent=Decimal("35.0"),
            max_drawdown_percent=Decimal("15.0")
        )
        
        # Record trade
        tracker.record_trade(
            strategy_id="momentum",
            symbol="AAPL",
            side="LONG",
            quantity=Decimal("100"),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            entry_time=entry_time,
            exit_time=exit_time
        )
        
        # Check if strategy should be disabled
        if not tracker.is_strategy_active("momentum"):
            logger.warning("Momentum strategy auto-disabled")
        
        # Get rankings
        rankings = tracker.get_strategy_rankings()
        best = rankings[0] if rankings else None
    """
    
    def __init__(
        self,
        min_sharpe_ratio: Decimal = Decimal("0.5"),
        max_consecutive_losses: int = 5,
        min_win_rate_percent: Decimal = Decimal("35.0"),
        max_drawdown_percent: Decimal = Decimal("15.0"),
        min_trades_for_evaluation: int = 10,
        lookback_days: int = 30
    ):
        """
        Initialize strategy performance tracker.
        
        Args:
            min_sharpe_ratio: Minimum acceptable Sharpe ratio
            max_consecutive_losses: Max consecutive losing trades
            min_win_rate_percent: Minimum win rate (%)
            max_drawdown_percent: Maximum drawdown (%)
            min_trades_for_evaluation: Min trades before evaluating
            lookback_days: Days to look back for recent performance
        """
        self.min_sharpe = min_sharpe_ratio
        self.max_consecutive_losses = max_consecutive_losses
        self.min_win_rate = min_win_rate_percent
        self.max_drawdown = max_drawdown_percent
        self.min_trades = min_trades_for_evaluation
        self.lookback_days = lookback_days
        
        self.logger = get_logger(LogStream.ANALYTICS)
        
        # Trade history per strategy
        self.trades: Dict[str, List[TradeRecord]] = {}
        
        # Strategy status
        self.status: Dict[str, StrategyStatus] = {}
        
        # Consecutive loss tracking
        self.consecutive_losses: Dict[str, int] = {}
        
        self.logger.info("StrategyPerformanceTracker initialized", extra={
            "min_sharpe": str(min_sharpe_ratio),
            "max_consecutive_losses": max_consecutive_losses,
            "min_win_rate": str(min_win_rate_percent)
        })
    
    # ========================================================================
    # TRADE RECORDING
    # ========================================================================
    
    def record_trade(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        exit_price: Decimal,
        entry_time: datetime,
        exit_time: datetime
    ):
        """Record a completed trade."""
        # Calculate P&L
        if side == "LONG":
            pnl = (exit_price - entry_price) * quantity
            pnl_percent = (exit_price - entry_price) / entry_price * Decimal("100")
        else:  # SHORT
            pnl = (entry_price - exit_price) * quantity
            pnl_percent = (entry_price - exit_price) / entry_price * Decimal("100")
        
        # Calculate duration
        duration = int((exit_time - entry_time).total_seconds())
        
        # Create record
        record = TradeRecord(
            timestamp=exit_time,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_percent=pnl_percent,
            duration_seconds=duration
        )
        
        # Store trade
        if strategy_id not in self.trades:
            self.trades[strategy_id] = []
            self.status[strategy_id] = StrategyStatus.ACTIVE
            self.consecutive_losses[strategy_id] = 0
        
        self.trades[strategy_id].append(record)
        
        # Update consecutive losses
        if pnl < 0:
            self.consecutive_losses[strategy_id] += 1
        else:
            self.consecutive_losses[strategy_id] = 0
        
        # Check cutoff conditions
        self._check_auto_cutoff(strategy_id)
    
    # ========================================================================
    # PERFORMANCE CALCULATION
    # ========================================================================
    
    def calculate_metrics(self, strategy_id: str) -> Optional[StrategyMetrics]:
        """Calculate performance metrics for strategy."""
        if strategy_id not in self.trades or not self.trades[strategy_id]:
            return None
        
        trades = self.trades[strategy_id]
        
        # Basic counts
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.pnl > 0)
        losing_trades = sum(1 for t in trades if t.pnl < 0)
        
        # Win rate
        win_rate = Decimal(winning_trades) / Decimal(total_trades) * Decimal("100") if total_trades > 0 else Decimal("0")
        
        # P&L
        total_pnl = sum(t.pnl for t in trades)
        
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl < 0]
        
        avg_win = sum(wins) / Decimal(len(wins)) if wins else Decimal("0")
        avg_loss = sum(losses) / Decimal(len(losses)) if losses else Decimal("0")
        largest_win = max(wins) if wins else Decimal("0")
        largest_loss = min(losses) if losses else Decimal("0")
        
        # Profit factor
        gross_profit = sum(wins) if wins else Decimal("0")
        gross_loss = abs(sum(losses)) if losses else Decimal("0")
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("0")
        
        # Sharpe ratio (simplified)
        returns = [t.pnl_percent for t in trades]
        if len(returns) >= 2:
            mean_return = sum(returns) / Decimal(len(returns))
            variance = sum((r - mean_return) ** 2 for r in returns) / Decimal(len(returns))
            std_dev = variance.sqrt() if variance > 0 else Decimal("0")
            sharpe = (mean_return / std_dev) if std_dev > 0 else Decimal("0")
        else:
            sharpe = Decimal("0")
        
        # Max drawdown
        cumulative_pnl = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        
        for trade in trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = (peak - cumulative_pnl) / peak * Decimal("100") if peak > 0 else Decimal("0")
            if drawdown > max_dd:
                max_dd = drawdown
        
        return StrategyMetrics(
            strategy_id=strategy_id,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            consecutive_losses=self.consecutive_losses.get(strategy_id, 0),
            status=self.status.get(strategy_id, StrategyStatus.ACTIVE)
        )
    
    # ========================================================================
    # AUTO-CUTOFF
    # ========================================================================
    
    def _check_auto_cutoff(self, strategy_id: str):
        """Check if strategy should be auto-disabled."""
        trades = self.trades.get(strategy_id, [])
        
        # Need minimum trades for evaluation
        if len(trades) < self.min_trades:
            return
        
        metrics = self.calculate_metrics(strategy_id)
        if not metrics:
            return
        
        # Check consecutive losses
        if self.consecutive_losses[strategy_id] >= self.max_consecutive_losses:
            self._disable_strategy(
                strategy_id,
                StrategyStatus.DISABLED_PERFORMANCE,
                f"Consecutive losses: {self.consecutive_losses[strategy_id]}"
            )
            return
        
        # Check Sharpe ratio
        if metrics.sharpe_ratio < self.min_sharpe:
            self._disable_strategy(
                strategy_id,
                StrategyStatus.DISABLED_PERFORMANCE,
                f"Sharpe ratio {metrics.sharpe_ratio:.2f} below {self.min_sharpe}"
            )
            return
        
        # Check win rate
        if metrics.win_rate < self.min_win_rate:
            self._disable_strategy(
                strategy_id,
                StrategyStatus.DISABLED_PERFORMANCE,
                f"Win rate {metrics.win_rate:.1f}% below {self.min_win_rate}%"
            )
            return
        
        # Check drawdown
        if metrics.max_drawdown > self.max_drawdown:
            self._disable_strategy(
                strategy_id,
                StrategyStatus.DISABLED_RISK,
                f"Drawdown {metrics.max_drawdown:.1f}% exceeds {self.max_drawdown}%"
            )
            return
    
    def _disable_strategy(
        self,
        strategy_id: str,
        status: StrategyStatus,
        reason: str
    ):
        """Disable a strategy."""
        self.status[strategy_id] = status
        
        self.logger.warning(f"Strategy DISABLED: {strategy_id}", extra={
            "reason": reason,
            "status": status.value
        })
    
    # ========================================================================
    # STRATEGY MANAGEMENT
    # ========================================================================
    
    def is_strategy_active(self, strategy_id: str) -> bool:
        """Check if strategy is active."""
        return self.status.get(strategy_id, StrategyStatus.ACTIVE) == StrategyStatus.ACTIVE
    
    def enable_strategy(self, strategy_id: str):
        """Manually enable a strategy."""
        self.status[strategy_id] = StrategyStatus.ACTIVE
        self.consecutive_losses[strategy_id] = 0
    
    def disable_strategy(self, strategy_id: str):
        """Manually disable a strategy."""
        self.status[strategy_id] = StrategyStatus.DISABLED_MANUAL
    
    # ========================================================================
    # RANKINGS
    # ========================================================================
    
    def get_strategy_rankings(self) -> List[StrategyMetrics]:
        """Get strategies ranked by Sharpe ratio."""
        all_metrics = []
        
        for strategy_id in self.trades.keys():
            metrics = self.calculate_metrics(strategy_id)
            if metrics:
                all_metrics.append(metrics)
        
        # Sort by Sharpe ratio (descending)
        all_metrics.sort(key=lambda m: m.sharpe_ratio, reverse=True)
        
        return all_metrics
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_statistics(self) -> Dict:
        """Get tracker statistics."""
        return {
            "total_strategies": len(self.trades),
            "active_strategies": sum(1 for s in self.status.values() if s == StrategyStatus.ACTIVE),
            "disabled_strategies": sum(1 for s in self.status.values() if s != StrategyStatus.ACTIVE),
            "total_trades": sum(len(trades) for trades in self.trades.values()),
            "strategies": {
                sid: self.calculate_metrics(sid).to_dict()
                for sid in self.trades.keys()
            }
        }
