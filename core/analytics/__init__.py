"""
Performance Analytics System (+5 Safety Points)

COMPREHENSIVE ANALYTICS:
- Performance tracking (Sharpe, Sortino, drawdown)
- Slippage analysis (by symbol, time, size)
- Trade attribution (by strategy, signal, time)

DESIGN STANDARDS:
- Industry-standard metrics
- Multi-dimensional analysis
- Actionable insights

USAGE:
    from core.analytics import (
        PerformanceTracker,
        SlippageAnalyzer,
        TradeAttributionAnalyzer,
        TradeResult
    )
    
    # Initialize trackers
    performance = PerformanceTracker(
        starting_equity=Decimal("10000")
    )
    
    slippage = SlippageAnalyzer(
        alert_threshold_bps=50
    )
    
    attribution = TradeAttributionAnalyzer()
    
    # Record trade
    trade = TradeResult(...)
    performance.add_trade(trade)
    attribution.add_trade(trade)
    
    # Record execution
    slippage.record_execution(
        symbol="AAPL",
        side="BUY",
        expected_price=Decimal("185.00"),
        actual_price=Decimal("185.10"),
        quantity=Decimal("100")
    )
    
    # Get metrics
    metrics = performance.get_metrics()
    print(f"Sharpe: {metrics.sharpe_ratio}")
    print(f"Max DD: {metrics.max_drawdown}%")
    
    # Get slippage stats
    slippage_stats = slippage.get_overall_statistics()
    print(f"Avg slippage: {slippage_stats.avg_slippage_bps} bps")
    
    # Get attribution
    by_strategy = attribution.get_attribution_by_strategy()
    for strategy, attr in by_strategy.items():
        print(f"{strategy}: ${attr.net_pnl}")
"""

# ============================================================================
# PERFORMANCE TRACKING
# ============================================================================

from core.analytics.performance import (
    PerformanceTracker,
    PerformanceMetrics,
    TradeResult
)

# ============================================================================
# SLIPPAGE ANALYSIS
# ============================================================================

from core.analytics.slippage import (
    SlippageAnalyzer,
    SlippageRecord,
    SlippageStatistics
)

# ============================================================================
# TRADE ATTRIBUTION
# ============================================================================

from core.analytics.attribution import (
    TradeAttributionAnalyzer,
    AttributionMetrics
)


__all__ = [
    # Performance tracking
    "PerformanceTracker",
    "PerformanceMetrics",
    "TradeResult",
    
    # Slippage analysis
    "SlippageAnalyzer",
    "SlippageRecord",
    "SlippageStatistics",
    
    # Trade attribution
    "TradeAttributionAnalyzer",
    "AttributionMetrics",
]
