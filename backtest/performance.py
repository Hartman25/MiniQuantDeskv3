"""
Performance analytics for backtesting.

LEAN COMPATIBILITY:
Based on QuantConnect's Statistics classes.

METRICS:
- Returns (total, annualized, daily)
- Risk metrics (Sharpe, Sortino, max drawdown)
- Trade statistics (win rate, profit factor)
- Risk-adjusted returns
- Benchmark comparison

Institutional-grade calculations.
"""

from typing import List, Dict, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from dataclasses import dataclass
import math

from core.logging import get_logger, LogStream


# ============================================================================
# PERFORMANCE RESULTS
# ============================================================================

@dataclass
class PerformanceMetrics:
    """Complete performance metrics."""
    
    # Returns
    total_return: Decimal
    annualized_return: Decimal
    daily_returns_mean: Decimal
    daily_returns_std: Decimal
    
    # Risk metrics
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    max_drawdown: Decimal
    max_drawdown_duration_days: int
    calmar_ratio: Decimal
    
    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    
    # Equity curve
    final_equity: Decimal
    peak_equity: Decimal
    
    # Costs
    total_commission: Decimal
    commission_pct_of_total_value: Decimal
    
    # Time
    start_date: datetime
    end_date: datetime
    duration_days: int


# ============================================================================
# PERFORMANCE ANALYZER
# ============================================================================

class PerformanceAnalyzer:
    """
    Performance analyzer for backtesting.
    
    CALCULATIONS:
    - Real-time equity tracking
    - Daily returns
    - Drawdown tracking
    - Statistical metrics
    
    USAGE:
        analyzer = PerformanceAnalyzer(starting_equity=100000)
        
        for each bar:
            analyzer.update(timestamp, current_equity)
        
        metrics = analyzer.get_metrics()
    """
    
    def __init__(self, starting_equity: Decimal):
        """
        Initialize analyzer.
        
        Args:
            starting_equity: Starting portfolio value
        """
        self.starting_equity = starting_equity
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Equity curve: [(timestamp, equity)]
        self.equity_curve: List[tuple] = []
        
        # Daily returns
        self.daily_returns: List[Decimal] = []
        
        # Drawdown tracking
        self.peak_equity = starting_equity
        self.max_drawdown = Decimal("0")
        self.current_drawdown_start: Optional[datetime] = None
        self.max_drawdown_duration = timedelta(0)
        
        # Trade tracking
        self.trade_pnls: List[Decimal] = []
        
        self.logger.info("PerformanceAnalyzer initialized", extra={
            "starting_equity": float(starting_equity)
        })
    
    def update(self, timestamp: datetime, current_equity: Decimal):
        """
        Update with new equity value.
        
        Args:
            timestamp: Current timestamp
            current_equity: Current portfolio value
        """
        # Add to equity curve
        self.equity_curve.append((timestamp, current_equity))
        
        # Calculate daily return
        if len(self.equity_curve) > 1:
            prev_equity = self.equity_curve[-2][1]
            daily_return = (current_equity - prev_equity) / prev_equity
            self.daily_returns.append(daily_return)
        
        # Update drawdown
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self.current_drawdown_start = None
        else:
            drawdown = (self.peak_equity - current_equity) / self.peak_equity
            
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown
            
            # Track drawdown duration
            if self.current_drawdown_start is None:
                self.current_drawdown_start = timestamp
            else:
                duration = timestamp - self.current_drawdown_start
                if duration > self.max_drawdown_duration:
                    self.max_drawdown_duration = duration
    
    def add_trade(self, pnl: Decimal):
        """
        Add completed trade P&L.
        
        Args:
            pnl: Trade profit/loss
        """
        self.trade_pnls.append(pnl)
    
    def get_metrics(
        self,
        total_commission: Decimal = Decimal("0"),
        risk_free_rate: Decimal = Decimal("0.02")
    ) -> PerformanceMetrics:
        """
        Calculate complete performance metrics.
        
        Args:
            total_commission: Total commission paid
            risk_free_rate: Annual risk-free rate (default: 2%)
            
        Returns:
            PerformanceMetrics
        """
        if not self.equity_curve:
            raise ValueError("No equity data to analyze")
        
        start_date = self.equity_curve[0][0]
        end_date = self.equity_curve[-1][0]
        final_equity = self.equity_curve[-1][1]
        
        duration_days = (end_date - start_date).days
        duration_years = duration_days / 365.25
        
        # Returns
        total_return = (final_equity - self.starting_equity) / self.starting_equity
        
        if duration_years > 0:
            annualized_return = ((1 + total_return) ** (1 / duration_years)) - 1
        else:
            annualized_return = Decimal("0")
        
        # Daily returns statistics
        if self.daily_returns:
            returns_mean = sum(self.daily_returns) / len(self.daily_returns)
            
            # Standard deviation
            variance = sum((r - returns_mean) ** 2 for r in self.daily_returns) / len(self.daily_returns)
            returns_std = Decimal(str(math.sqrt(float(variance))))
        else:
            returns_mean = Decimal("0")
            returns_std = Decimal("0")
        
        # Sharpe ratio (annualized)
        if returns_std > 0:
            daily_rf_rate = risk_free_rate / Decimal("252")  # Trading days
            excess_return = returns_mean - daily_rf_rate
            sharpe = excess_return / returns_std * Decimal(str(math.sqrt(252)))
        else:
            sharpe = Decimal("0")
        
        # Sortino ratio (only downside deviation)
        downside_returns = [r for r in self.daily_returns if r < 0]
        if downside_returns:
            downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_std = Decimal(str(math.sqrt(float(downside_variance))))
            
            if downside_std > 0:
                sortino = returns_mean / downside_std * Decimal(str(math.sqrt(252)))
            else:
                sortino = Decimal("0")
        else:
            sortino = Decimal("0")
        
        # Calmar ratio (return / max drawdown)
        if self.max_drawdown > 0:
            calmar = annualized_return / self.max_drawdown
        else:
            calmar = Decimal("0")
        
        # Trade statistics
        winning_trades = [p for p in self.trade_pnls if p > 0]
        losing_trades = [p for p in self.trade_pnls if p < 0]
        
        total_trades = len(self.trade_pnls)
        num_winning = len(winning_trades)
        num_losing = len(losing_trades)
        
        win_rate = Decimal(num_winning) / Decimal(total_trades) if total_trades > 0 else Decimal("0")
        
        avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else Decimal("0")
        avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else Decimal("0")
        
        # Profit factor (gross profit / gross loss)
        gross_profit = sum(winning_trades) if winning_trades else Decimal("0")
        gross_loss = abs(sum(losing_trades)) if losing_trades else Decimal("0")
        
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = Decimal("0") if gross_profit == 0 else Decimal("999")  # Infinite
        
        largest_win = max(winning_trades) if winning_trades else Decimal("0")
        largest_loss = min(losing_trades) if losing_trades else Decimal("0")
        
        # Commission analysis
        total_traded_value = final_equity + abs(self.starting_equity - final_equity)
        commission_pct = total_commission / total_traded_value if total_traded_value > 0 else Decimal("0")
        
        return PerformanceMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            daily_returns_mean=returns_mean,
            daily_returns_std=returns_std,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=self.max_drawdown,
            max_drawdown_duration_days=self.max_drawdown_duration.days,
            calmar_ratio=calmar,
            total_trades=total_trades,
            winning_trades=num_winning,
            losing_trades=num_losing,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            largest_win=largest_win,
            largest_loss=largest_loss,
            final_equity=final_equity,
            peak_equity=self.peak_equity,
            total_commission=total_commission,
            commission_pct_of_total_value=commission_pct,
            start_date=start_date,
            end_date=end_date,
            duration_days=duration_days
        )
    
    def get_equity_curve(self) -> List[tuple]:
        """Get equity curve as list of (timestamp, equity)."""
        return self.equity_curve.copy()
