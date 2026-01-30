"""
Daily summary generator - EOD reports.

ARCHITECTURE:
- Trade aggregation
- Performance metrics
- Win/loss analysis
- Position summary
- Risk metrics

Based on professional trading reports.
"""

from typing import Dict, List
from decimal import Decimal
from datetime import datetime, date
from dataclasses import dataclass

from core.state import Position, PositionStore, TransactionLog
from core.logging import get_logger, LogStream


# ============================================================================
# DAILY STATS
# ============================================================================

@dataclass
class DailyStats:
    """Daily performance statistics."""
    date: date
    
    # Trading stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    break_even_trades: int
    
    # P&L
    gross_profit: Decimal
    gross_loss: Decimal
    net_pnl: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    
    # Returns
    total_return_pct: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    win_rate: Decimal
    
    # Risk
    max_drawdown: Decimal
    sharpe_ratio: Decimal
    
    # Position info
    positions_opened: int
    positions_closed: int
    positions_open_eod: int


# ============================================================================
# SUMMARY GENERATOR
# ============================================================================

class DailySummaryGenerator:
    """
    Daily summary generator.
    
    USAGE:
        generator = DailySummaryGenerator(
            transaction_log=tx_log,
            position_store=pos_store
        )
        
        summary = generator.generate_summary(date.today())
        
        report = generator.format_report(summary)
    """
    
    def __init__(
        self,
        transaction_log: TransactionLog,
        position_store: PositionStore
    ):
        """Initialize generator."""
        self.transaction_log = transaction_log
        self.position_store = position_store
        self.logger = get_logger(LogStream.SYSTEM)
    
    def generate_summary(self, target_date: date) -> DailyStats:
        """
        Generate daily summary.
        
        Args:
            target_date: Date to summarize
            
        Returns:
            DailyStats
        """
        # TODO: Read transaction log and calculate stats
        # This is a simplified version - full implementation would:
        # 1. Parse transaction log for target date
        # 2. Group by order_id
        # 3. Calculate P&L per trade
        # 4. Aggregate statistics
        
        # Placeholder implementation
        stats = DailyStats(
            date=target_date,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            break_even_trades=0,
            gross_profit=Decimal("0"),
            gross_loss=Decimal("0"),
            net_pnl=Decimal("0"),
            largest_win=Decimal("0"),
            largest_loss=Decimal("0"),
            total_return_pct=Decimal("0"),
            avg_win=Decimal("0"),
            avg_loss=Decimal("0"),
            win_rate=Decimal("0"),
            max_drawdown=Decimal("0"),
            sharpe_ratio=Decimal("0"),
            positions_opened=0,
            positions_closed=0,
            positions_open_eod=len(self.position_store.get_all())
        )
        
        return stats
    
    def format_report(self, stats: DailyStats) -> str:
        """
        Format summary as text report.
        
        Args:
            stats: Daily statistics
            
        Returns:
            Formatted report string
        """
        report = f"""
╔══════════════════════════════════════════════════════════╗
║           DAILY TRADING SUMMARY - {stats.date}           ║
╚══════════════════════════════════════════════════════════╝

📊 TRADING ACTIVITY
  Total Trades:        {stats.total_trades}
  Winning Trades:      {stats.winning_trades}
  Losing Trades:       {stats.losing_trades}
  Win Rate:            {stats.win_rate:.1%}

💰 PROFIT & LOSS
  Net P&L:             ${stats.net_pnl:,.2f}
  Gross Profit:        ${stats.gross_profit:,.2f}
  Gross Loss:          ${stats.gross_loss:,.2f}
  Largest Win:         ${stats.largest_win:,.2f}
  Largest Loss:        ${stats.largest_loss:,.2f}
  Average Win:         ${stats.avg_win:,.2f}
  Average Loss:        ${stats.avg_loss:,.2f}

📈 PERFORMANCE
  Total Return:        {stats.total_return_pct:+.2%}
  Max Drawdown:        {stats.max_drawdown:.2%}
  Sharpe Ratio:        {stats.sharpe_ratio:.2f}

📋 POSITIONS
  Opened Today:        {stats.positions_opened}
  Closed Today:        {stats.positions_closed}
  Open at EOD:         {stats.positions_open_eod}

═══════════════════════════════════════════════════════════
"""
        return report
    
    def format_discord_summary(self, stats: DailyStats) -> Dict:
        """
        Format summary for Discord notification.
        
        Args:
            stats: Daily statistics
            
        Returns:
            Dict for Discord notifier
        """
        return {
            "date": stats.date.isoformat(),
            "trades": stats.total_trades,
            "pnl": float(stats.net_pnl),
            "win_rate": float(stats.win_rate),
            "largest_win": float(stats.largest_win),
            "largest_loss": float(stats.largest_loss),
            "sharpe": float(stats.sharpe_ratio)
        }
