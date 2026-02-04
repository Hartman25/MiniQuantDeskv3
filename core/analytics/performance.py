"""
Performance tracking and metrics calculation.

ARCHITECTURE:
- Track returns and P&L
- Calculate Sharpe and Sortino ratios
- Maximum drawdown tracking
- Win rate and profit factor
- Rolling performance windows

DESIGN PRINCIPLE:
Can't optimize what you don't measure.

METRICS:
- Sharpe Ratio: Risk-adjusted return
- Sortino Ratio: Downside risk-adjusted return
- Max Drawdown: Largest peak-to-trough decline
- Win Rate: % of winning trades
- Profit Factor: Gross profit / Gross loss

Based on industry-standard performance metrics.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Dict
from collections import deque
import math

from core.logging import get_logger, LogStream


# ============================================================================
# TRADE RESULT
# ============================================================================

@dataclass
class TradeResult:
    """Individual trade result."""
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    side: str  # "LONG" or "SHORT"
    pnl: Decimal
    pnl_percent: Decimal
    commission: Decimal
    duration_hours: float
    strategy: Optional[str] = None
    signal_type: Optional[str] = None

    # Phase 2 (P2-INV-15): Signal vs execution timing + slippage
    signal_time: Optional[datetime] = None   # when strategy decided to trade
    signal_price: Optional[Decimal] = None   # price at signal generation

    def is_winner(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl > 0

    @property
    def entry_slippage(self) -> Optional[Decimal]:
        """Slippage = entry_price - signal_price (positive = adverse)."""
        if self.signal_price is not None:
            return self.entry_price - self.signal_price
        return None

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        d = {
            "symbol": self.symbol,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price),
            "quantity": str(self.quantity),
            "side": self.side,
            "pnl": str(self.pnl),
            "pnl_percent": str(self.pnl_percent),
            "commission": str(self.commission),
            "duration_hours": round(self.duration_hours, 2),
            "strategy": self.strategy,
            "signal_type": self.signal_type,
        }
        if self.signal_time is not None:
            d["signal_time"] = self.signal_time.isoformat()
        if self.signal_price is not None:
            d["signal_price"] = str(self.signal_price)
        if self.entry_slippage is not None:
            d["entry_slippage"] = str(self.entry_slippage)
        return d


# ============================================================================
# PERFORMANCE METRICS
# ============================================================================

@dataclass
class PerformanceMetrics:
    """Performance metrics for a period."""
    period_start: datetime
    period_end: datetime
    
    # Returns
    total_return: Decimal
    annualized_return: Decimal
    
    # Risk metrics
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    max_drawdown_duration_days: float
    
    # Trade stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    
    # P&L
    gross_profit: Decimal
    gross_loss: Decimal
    net_profit: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    
    # Other
    avg_trade_duration_hours: float
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_return": str(self.total_return),
            "annualized_return": str(self.annualized_return),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "max_drawdown": str(self.max_drawdown),
            "max_drawdown_duration_days": round(self.max_drawdown_duration_days, 1),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2),
            "gross_profit": str(self.gross_profit),
            "gross_loss": str(self.gross_loss),
            "net_profit": str(self.net_profit),
            "avg_win": str(self.avg_win),
            "avg_loss": str(self.avg_loss),
            "largest_win": str(self.largest_win),
            "largest_loss": str(self.largest_loss),
            "avg_trade_duration_hours": round(self.avg_trade_duration_hours, 1)
        }


# ============================================================================
# PERFORMANCE TRACKER
# ============================================================================

class PerformanceTracker:
    """
    Track and calculate performance metrics.
    
    RESPONSIBILITIES:
    - Record trades and returns
    - Calculate Sharpe/Sortino ratios
    - Track maximum drawdown
    - Calculate win rate and profit factor
    - Generate performance reports
    
    METRICS CALCULATED:
    - Sharpe Ratio = (Return - RiskFreeRate) / StdDev(Returns)
    - Sortino Ratio = (Return - RiskFreeRate) / StdDev(DownsideReturns)
    - Max Drawdown = Max(Peak - Trough) / Peak
    - Win Rate = Winning Trades / Total Trades
    - Profit Factor = Gross Profit / Gross Loss
    
    USAGE:
        tracker = PerformanceTracker(
            starting_equity=Decimal("10000"),
            risk_free_rate=0.04  # 4% annual
        )
        
        # Record trades
        tracker.add_trade(TradeResult(...))
        
        # Update equity daily
        tracker.update_equity(current_equity)
        
        # Get metrics
        metrics = tracker.get_metrics()
        print(f"Sharpe: {metrics.sharpe_ratio}")
        print(f"Max Drawdown: {metrics.max_drawdown}%")
    """
    
    def __init__(
        self,
        starting_equity: Decimal,
        risk_free_rate: float = 0.04,  # 4% annual
        lookback_days: int = 252  # 1 year trading days
    ):
        """
        Initialize performance tracker.
        
        Args:
            starting_equity: Starting equity
            risk_free_rate: Annual risk-free rate (for Sharpe/Sortino)
            lookback_days: Days to keep in rolling window
        """
        self.starting_equity = starting_equity
        self.risk_free_rate = risk_free_rate
        self.lookback_days = lookback_days
        
        self.logger = get_logger(LogStream.ANALYTICS)
        
        # State
        self.current_equity = starting_equity
        self.peak_equity = starting_equity
        
        # History
        self.trades: List[TradeResult] = []
        self.daily_returns: deque = deque(maxlen=lookback_days)  # (date, return)
        self.equity_curve: deque = deque(maxlen=lookback_days)  # (date, equity)
        
        # Drawdown tracking
        self.max_drawdown = Decimal("0")
        self.current_drawdown_start: Optional[datetime] = None
        self.max_drawdown_duration = timedelta(0)
        
        # Start date
        self.start_date = datetime.now(timezone.utc)
        
        self.logger.info("PerformanceTracker initialized", extra={
            "starting_equity": str(starting_equity),
            "risk_free_rate": risk_free_rate
        })
    
    # ========================================================================
    # TRADE RECORDING
    # ========================================================================
    
    def add_trade(self, trade: TradeResult):
        """Record a completed trade."""
        self.trades.append(trade)
        
        self.logger.debug(f"Trade recorded: {trade.symbol}", extra={
            "pnl": str(trade.pnl),
            "pnl_percent": str(trade.pnl_percent)
        })
    
    # ========================================================================
    # EQUITY UPDATES
    # ========================================================================
    
    def update_equity(
        self,
        new_equity: Decimal,
        timestamp: Optional[datetime] = None
    ):
        """
        Update current equity and calculate returns.
        
        Args:
            new_equity: Current equity value
            timestamp: Timestamp (defaults to now)
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        
        # Calculate daily return
        if len(self.equity_curve) > 0:
            prev_equity = self.equity_curve[-1][1]
            if prev_equity > 0:
                daily_return = float((new_equity - prev_equity) / prev_equity)
                self.daily_returns.append((timestamp, daily_return))
        
        # Update equity curve
        self.equity_curve.append((timestamp, new_equity))
        
        # Update peak and drawdown
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
            self.current_drawdown_start = None
        else:
            # In drawdown
            drawdown = (self.peak_equity - new_equity) / self.peak_equity * Decimal("100")
            
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown
            
            if self.current_drawdown_start is None:
                self.current_drawdown_start = timestamp
            else:
                duration = timestamp - self.current_drawdown_start
                if duration > self.max_drawdown_duration:
                    self.max_drawdown_duration = duration
        
        self.current_equity = new_equity
    
    # ========================================================================
    # METRICS CALCULATION
    # ========================================================================
    
    def get_metrics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> PerformanceMetrics:
        """
        Calculate performance metrics for a period.
        
        Args:
            start_date: Period start (defaults to tracker start)
            end_date: Period end (defaults to now)
            
        Returns:
            PerformanceMetrics object
        """
        start_date = start_date or self.start_date
        end_date = end_date or datetime.now(timezone.utc)
        
        # Filter trades for period
        period_trades = [
            t for t in self.trades
            if start_date <= t.exit_time <= end_date
        ]
        
        # Calculate return metrics
        total_return = (self.current_equity - self.starting_equity) / self.starting_equity * Decimal("100")
        
        days_elapsed = (end_date - start_date).days or 1
        years_elapsed = days_elapsed / 365.25
        annualized_return = (float(total_return) / years_elapsed) if years_elapsed > 0 else 0
        
        # Calculate risk metrics
        sharpe = self._calculate_sharpe_ratio()
        sortino = self._calculate_sortino_ratio()
        
        # Calculate trade stats
        if period_trades:
            winning_trades = [t for t in period_trades if t.is_winner()]
            losing_trades = [t for t in period_trades if not t.is_winner()]
            
            win_rate = len(winning_trades) / len(period_trades) if period_trades else 0.0
            
            gross_profit = sum(t.pnl for t in winning_trades)
            gross_loss = abs(sum(t.pnl for t in losing_trades))
            
            profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float('inf')
            
            avg_win = gross_profit / len(winning_trades) if winning_trades else Decimal("0")
            avg_loss = gross_loss / len(losing_trades) if losing_trades else Decimal("0")
            
            largest_win = max((t.pnl for t in winning_trades), default=Decimal("0"))
            largest_loss = min((t.pnl for t in losing_trades), default=Decimal("0"))
            
            avg_duration = sum(t.duration_hours for t in period_trades) / len(period_trades)
        else:
            winning_trades = []
            losing_trades = []
            win_rate = 0.0
            gross_profit = Decimal("0")
            gross_loss = Decimal("0")
            profit_factor = 0.0
            avg_win = Decimal("0")
            avg_loss = Decimal("0")
            largest_win = Decimal("0")
            largest_loss = Decimal("0")
            avg_duration = 0.0
        
        return PerformanceMetrics(
            period_start=start_date,
            period_end=end_date,
            total_return=total_return,
            annualized_return=Decimal(str(annualized_return)),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=self.max_drawdown,
            max_drawdown_duration_days=self.max_drawdown_duration.days,
            total_trades=len(period_trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            profit_factor=profit_factor,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_profit=gross_profit - gross_loss,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            avg_trade_duration_hours=avg_duration
        )
    
    def _calculate_sharpe_ratio(self) -> float:
        """Calculate Sharpe ratio (annualized)."""
        if len(self.daily_returns) < 2:
            return 0.0
        
        returns = [r[1] for r in self.daily_returns]
        
        avg_return = sum(returns) / len(returns)
        std_dev = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
        
        if std_dev == 0:
            return 0.0
        
        # Annualize (252 trading days)
        daily_rf = self.risk_free_rate / 252
        sharpe = (avg_return - daily_rf) / std_dev * math.sqrt(252)
        
        return sharpe
    
    def _calculate_sortino_ratio(self) -> float:
        """Calculate Sortino ratio (annualized, downside deviation only)."""
        if len(self.daily_returns) < 2:
            return 0.0
        
        returns = [r[1] for r in self.daily_returns]
        
        avg_return = sum(returns) / len(returns)
        
        # Downside deviation (only negative returns)
        downside_returns = [r for r in returns if r < 0]
        
        if not downside_returns:
            return float('inf')  # No downside = infinite Sortino
        
        downside_dev = math.sqrt(sum(r ** 2 for r in downside_returns) / len(downside_returns))
        
        if downside_dev == 0:
            return 0.0
        
        # Annualize
        daily_rf = self.risk_free_rate / 252
        sortino = (avg_return - daily_rf) / downside_dev * math.sqrt(252)
        
        return sortino
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_statistics(self) -> Dict:
        """Get comprehensive statistics."""
        metrics = self.get_metrics()
        
        return {
            **metrics.to_dict(),
            "current_equity": str(self.current_equity),
            "peak_equity": str(self.peak_equity),
            "starting_equity": str(self.starting_equity),
            "total_trades_all_time": len(self.trades),
            "days_tracked": (datetime.now(timezone.utc) - self.start_date).days
        }
