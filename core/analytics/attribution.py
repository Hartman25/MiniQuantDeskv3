"""
Trade attribution and P&L analysis.

ARCHITECTURE:
- P&L attribution by strategy
- P&L attribution by signal type
- P&L attribution by time of day
- P&L attribution by symbol
- Identify best/worst performers

DESIGN PRINCIPLE:
Know what works and what doesn't.

ATTRIBUTION:
Break down total P&L into components to understand sources of profit/loss.

EXAMPLE:
Total P&L: $1,000
- Momentum Strategy: $800 (80%)
- Mean Reversion: $200 (20%)

Momentum Breakdown:
- BUY_BREAKOUT: $600
- BUY_TREND: $200

Based on institutional attribution analysis.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, time
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from enum import Enum

from core.analytics.performance import TradeResult
from core.logging import get_logger, LogStream


# ============================================================================
# ATTRIBUTION BREAKDOWN
# ============================================================================

@dataclass
class AttributionBreakdown:
    """P&L attribution for a category."""
    category: str  # "strategy", "signal_type", "time_of_day", "symbol"
    subcategory: str  # e.g., "MOMENTUM", "BUY_BREAKOUT", "Morning", "AAPL"
    
    trade_count: int
    winning_trades: int
    losing_trades: int
    
    total_pnl: Decimal
    pnl_percent_of_total: Decimal  # % of total P&L
    
    win_rate: float
    avg_pnl_per_trade: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "trade_count": self.trade_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_pnl": str(self.total_pnl),
            "pnl_percent_of_total": str(self.pnl_percent_of_total),
            "win_rate": round(self.win_rate, 2),
            "avg_pnl_per_trade": str(self.avg_pnl_per_trade),
            "largest_win": str(self.largest_win),
            "largest_loss": str(self.largest_loss)
        }


# ============================================================================
# TRADE ATTRIBUTION ANALYZER
# ============================================================================

class TradeAttributionAnalyzer:
    """
    Analyze trade P&L attribution across multiple dimensions.
    
    RESPONSIBILITIES:
    - Attribute P&L by strategy
    - Attribute P&L by signal type
    - Attribute P&L by time of day
    - Attribute P&L by symbol
    - Identify top performers
    - Identify underperformers
    
    ANALYSIS DIMENSIONS:
    - Strategy: Which strategy makes money
    - Signal Type: Which entry/exit signals work
    - Time of Day: When to trade
    - Symbol: Which stocks to focus on
    
    USAGE:
        analyzer = TradeAttributionAnalyzer()
        
        # Add trades
        for trade in completed_trades:
            analyzer.add_trade(trade)
        
        # Get attribution
        by_strategy = analyzer.get_attribution_by_strategy()
        for strategy, breakdown in by_strategy.items():
            print(f"{strategy}: {breakdown.total_pnl}")
        
        # Find best performers
        top = analyzer.get_top_strategies(5)
        print(f"Best strategy: {top[0]}")
    """
    
    def __init__(self):
        """Initialize attribution analyzer."""
        self.logger = get_logger(LogStream.ANALYTICS)
        
        # Storage
        self.trades: List[TradeResult] = []
        
        self.logger.info("TradeAttributionAnalyzer initialized")
    
    # ========================================================================
    # TRADE RECORDING
    # ========================================================================
    
    def add_trade(self, trade: TradeResult):
        """Add a trade for attribution analysis."""
        self.trades.append(trade)
    
    def add_trades(self, trades: List[TradeResult]):
        """Add multiple trades."""
        self.trades.extend(trades)
    
    # ========================================================================
    # ATTRIBUTION BY STRATEGY
    # ========================================================================
    
    def get_attribution_by_strategy(self) -> Dict[str, AttributionBreakdown]:
        """
        Get P&L attribution by strategy.
        
        Returns:
            Dictionary of strategy -> attribution breakdown
        """
        # Group by strategy
        by_strategy = defaultdict(list)
        
        for trade in self.trades:
            strategy = trade.strategy or "UNKNOWN"
            by_strategy[strategy].append(trade)
        
        # Calculate total P&L for percentages
        total_pnl = sum(t.pnl for t in self.trades)
        
        # Build attribution
        attribution = {}
        
        for strategy, strategy_trades in by_strategy.items():
            attribution[strategy] = self._calculate_attribution(
                strategy_trades,
                total_pnl,
                "strategy",
                strategy
            )
        
        return attribution
    
    # ========================================================================
    # ATTRIBUTION BY SIGNAL TYPE
    # ========================================================================
    
    def get_attribution_by_signal_type(self) -> Dict[str, AttributionBreakdown]:
        """
        Get P&L attribution by signal type.
        
        Returns:
            Dictionary of signal_type -> attribution breakdown
        """
        by_signal = defaultdict(list)
        
        for trade in self.trades:
            signal = trade.signal_type or "UNKNOWN"
            by_signal[signal].append(trade)
        
        total_pnl = sum(t.pnl for t in self.trades)
        
        attribution = {}
        
        for signal, signal_trades in by_signal.items():
            attribution[signal] = self._calculate_attribution(
                signal_trades,
                total_pnl,
                "signal_type",
                signal
            )
        
        return attribution
    
    # ========================================================================
    # ATTRIBUTION BY TIME OF DAY
    # ========================================================================
    
    def get_attribution_by_time_of_day(self) -> Dict[str, AttributionBreakdown]:
        """
        Get P&L attribution by time of day.
        
        Returns:
            Dictionary of time_bucket -> attribution breakdown
        """
        # Define time buckets
        buckets = {
            "Market Open (09:30-10:00)": (time(9, 30), time(10, 0)),
            "Morning (10:00-11:30)": (time(10, 0), time(11, 30)),
            "Midday (11:30-14:00)": (time(11, 30), time(14, 0)),
            "Afternoon (14:00-15:30)": (time(14, 0), time(15, 30)),
            "Market Close (15:30-16:00)": (time(15, 30), time(16, 0))
        }
        
        # Group by bucket (using entry time)
        by_bucket = defaultdict(list)
        
        for trade in self.trades:
            entry_time = trade.entry_time.time()
            
            for bucket_name, (start, end) in buckets.items():
                if start <= entry_time < end:
                    by_bucket[bucket_name].append(trade)
                    break
        
        total_pnl = sum(t.pnl for t in self.trades)
        
        attribution = {}
        
        for bucket, bucket_trades in by_bucket.items():
            if bucket_trades:
                attribution[bucket] = self._calculate_attribution(
                    bucket_trades,
                    total_pnl,
                    "time_of_day",
                    bucket
                )
        
        return attribution
    
    # ========================================================================
    # ATTRIBUTION BY SYMBOL
    # ========================================================================
    
    def get_attribution_by_symbol(self) -> Dict[str, AttributionBreakdown]:
        """
        Get P&L attribution by symbol.
        
        Returns:
            Dictionary of symbol -> attribution breakdown
        """
        by_symbol = defaultdict(list)
        
        for trade in self.trades:
            by_symbol[trade.symbol].append(trade)
        
        total_pnl = sum(t.pnl for t in self.trades)
        
        attribution = {}
        
        for symbol, symbol_trades in by_symbol.items():
            attribution[symbol] = self._calculate_attribution(
                symbol_trades,
                total_pnl,
                "symbol",
                symbol
            )
        
        return attribution
    
    # ========================================================================
    # ATTRIBUTION CALCULATION
    # ========================================================================
    
    def _calculate_attribution(
        self,
        trades: List[TradeResult],
        total_pnl: Decimal,
        category: str,
        subcategory: str
    ) -> AttributionBreakdown:
        """Calculate attribution for a set of trades."""
        if not trades:
            return AttributionBreakdown(
                category=category,
                subcategory=subcategory,
                trade_count=0,
                winning_trades=0,
                losing_trades=0,
                total_pnl=Decimal("0"),
                pnl_percent_of_total=Decimal("0"),
                win_rate=0.0,
                avg_pnl_per_trade=Decimal("0"),
                largest_win=Decimal("0"),
                largest_loss=Decimal("0")
            )
        
        winners = [t for t in trades if t.is_winner()]
        losers = [t for t in trades if not t.is_winner()]
        
        group_pnl = sum(t.pnl for t in trades)
        
        pnl_pct = (group_pnl / total_pnl * Decimal("100")) if total_pnl != 0 else Decimal("0")
        
        win_rate = len(winners) / len(trades) if trades else 0.0
        
        avg_pnl = group_pnl / len(trades)
        
        largest_win = max((t.pnl for t in winners), default=Decimal("0"))
        largest_loss = min((t.pnl for t in losers), default=Decimal("0"))
        
        return AttributionBreakdown(
            category=category,
            subcategory=subcategory,
            trade_count=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            total_pnl=group_pnl,
            pnl_percent_of_total=pnl_pct,
            win_rate=win_rate,
            avg_pnl_per_trade=avg_pnl,
            largest_win=largest_win,
            largest_loss=largest_loss
        )
    
    # ========================================================================
    # TOP/BOTTOM PERFORMERS
    # ========================================================================
    
    def get_top_strategies(self, count: int = 5) -> List[Tuple[str, AttributionBreakdown]]:
        """
        Get top performing strategies by total P&L.
        
        Returns:
            List of (strategy, breakdown) tuples
        """
        attribution = self.get_attribution_by_strategy()
        
        sorted_attribution = sorted(
            attribution.items(),
            key=lambda x: x[1].total_pnl,
            reverse=True
        )
        
        return sorted_attribution[:count]
    
    def get_bottom_strategies(self, count: int = 5) -> List[Tuple[str, AttributionBreakdown]]:
        """
        Get worst performing strategies by total P&L.
        
        Returns:
            List of (strategy, breakdown) tuples
        """
        attribution = self.get_attribution_by_strategy()
        
        sorted_attribution = sorted(
            attribution.items(),
            key=lambda x: x[1].total_pnl,
            reverse=False
        )
        
        return sorted_attribution[:count]
    
    def get_top_signals(self, count: int = 5) -> List[Tuple[str, AttributionBreakdown]]:
        """Get top performing signal types."""
        attribution = self.get_attribution_by_signal_type()
        
        sorted_attribution = sorted(
            attribution.items(),
            key=lambda x: x[1].total_pnl,
            reverse=True
        )
        
        return sorted_attribution[:count]
    
    def get_top_symbols(self, count: int = 10) -> List[Tuple[str, AttributionBreakdown]]:
        """Get top performing symbols."""
        attribution = self.get_attribution_by_symbol()
        
        sorted_attribution = sorted(
            attribution.items(),
            key=lambda x: x[1].total_pnl,
            reverse=True
        )
        
        return sorted_attribution[:count]
    
    def get_best_times(self) -> List[Tuple[str, AttributionBreakdown]]:
        """Get best times of day to trade."""
        attribution = self.get_attribution_by_time_of_day()
        
        sorted_attribution = sorted(
            attribution.items(),
            key=lambda x: x[1].total_pnl,
            reverse=True
        )
        
        return sorted_attribution
    
    # ========================================================================
    # COMPREHENSIVE REPORTING
    # ========================================================================
    
    def get_comprehensive_report(self) -> Dict:
        """
        Get comprehensive attribution report.
        
        Returns:
            Dictionary with all attribution dimensions
        """
        return {
            "total_trades": len(self.trades),
            "total_pnl": str(sum(t.pnl for t in self.trades)),
            
            "by_strategy": {
                strategy: breakdown.to_dict()
                for strategy, breakdown in self.get_attribution_by_strategy().items()
            },
            
            "by_signal_type": {
                signal: breakdown.to_dict()
                for signal, breakdown in self.get_attribution_by_signal_type().items()
            },
            
            "by_time_of_day": {
                time_bucket: breakdown.to_dict()
                for time_bucket, breakdown in self.get_attribution_by_time_of_day().items()
            },
            
            "by_symbol": {
                symbol: breakdown.to_dict()
                for symbol, breakdown in self.get_attribution_by_symbol().items()
            },
            
            "top_performers": {
                "strategies": [
                    {"name": name, **breakdown.to_dict()}
                    for name, breakdown in self.get_top_strategies(5)
                ],
                "signals": [
                    {"name": name, **breakdown.to_dict()}
                    for name, breakdown in self.get_top_signals(5)
                ],
                "symbols": [
                    {"name": name, **breakdown.to_dict()}
                    for name, breakdown in self.get_top_symbols(10)
                ],
                "times": [
                    {"name": name, **breakdown.to_dict()}
                    for name, breakdown in self.get_best_times()
                ]
            },
            
            "bottom_performers": {
                "strategies": [
                    {"name": name, **breakdown.to_dict()}
                    for name, breakdown in self.get_bottom_strategies(5)
                ]
            }
        }
    
    # ========================================================================
    # FILTERING
    # ========================================================================
    
    def filter_by_strategy(self, strategy: str) -> 'TradeAttributionAnalyzer':
        """Create new analyzer filtered by strategy."""
        filtered = TradeAttributionAnalyzer()
        filtered.trades = [t for t in self.trades if t.strategy == strategy]
        return filtered
    
    def filter_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> 'TradeAttributionAnalyzer':
        """Create new analyzer filtered by date range."""
        filtered = TradeAttributionAnalyzer()
        filtered.trades = [
            t for t in self.trades
            if start_date <= t.exit_time <= end_date
        ]
        return filtered
