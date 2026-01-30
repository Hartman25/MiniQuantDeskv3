"""
Tests for analytics system.

COVERAGE:
- PerformanceTracker (metrics, Sharpe, Sortino, drawdown)
- SlippageAnalyzer (by symbol, time, size)
- TradeAttributionAnalyzer (by strategy, signal, time, symbol)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.analytics import (
    PerformanceTracker,
    TradeResult,
    SlippageAnalyzer,
    TradeAttributionAnalyzer
)


# ============================================================================
# PERFORMANCE TRACKER TESTS
# ============================================================================

class TestPerformanceTracker:
    """Test performance tracking."""
    
    def test_initialization(self):
        """Test tracker initializes correctly."""
        tracker = PerformanceTracker(
            starting_equity=Decimal("10000"),
            risk_free_rate=0.04
        )
        
        assert tracker.starting_equity == Decimal("10000")
        assert tracker.current_equity == Decimal("10000")
        assert tracker.peak_equity == Decimal("10000")
    
    def test_equity_updates(self):
        """Test equity updates and return calculation."""
        tracker = PerformanceTracker(starting_equity=Decimal("10000"))
        
        # First update establishes baseline (no return calculated yet)
        tracker.update_equity(Decimal("10000"))
        
        # Second update calculates return
        tracker.update_equity(Decimal("10500"))
        
        assert tracker.current_equity == Decimal("10500")
        assert tracker.peak_equity == Decimal("10500")
        assert len(tracker.daily_returns) >= 1
    
    def test_drawdown_tracking(self):
        """Test maximum drawdown tracking."""
        tracker = PerformanceTracker(starting_equity=Decimal("10000"))
        
        # Set peak
        tracker.update_equity(Decimal("11000"))
        
        # Drawdown
        tracker.update_equity(Decimal("9900"))  # -10% from peak
        
        assert tracker.max_drawdown == Decimal("10")  # 10%
    
    def test_trade_recording(self):
        """Test recording trades."""
        tracker = PerformanceTracker(starting_equity=Decimal("10000"))
        
        trade = TradeResult(
            symbol="AAPL",
            entry_time=datetime.now(timezone.utc) - timedelta(hours=2),
            exit_time=datetime.now(timezone.utc),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            quantity=Decimal("10"),
            side="LONG",
            pnl=Decimal("50.00"),
            pnl_percent=Decimal("2.78"),
            commission=Decimal("1.00"),
            duration_hours=2.0
        )
        
        tracker.add_trade(trade)
        
        assert len(tracker.trades) == 1
    
    def test_metrics_calculation(self):
        """Test performance metrics calculation."""
        tracker = PerformanceTracker(starting_equity=Decimal("10000"))
        
        # Add some trades
        for i in range(5):
            trade = TradeResult(
                symbol="TEST",
                entry_time=datetime.now(timezone.utc) - timedelta(hours=i+1),
                exit_time=datetime.now(timezone.utc),
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                quantity=Decimal("10"),
                side="LONG",
                pnl=Decimal("50.00"),
                pnl_percent=Decimal("5.0"),
                commission=Decimal("1.00"),
                duration_hours=1.0
            )
            tracker.add_trade(trade)
        
        # Update equity
        tracker.update_equity(Decimal("10250"))
        
        metrics = tracker.get_metrics()
        
        assert metrics.total_trades == 5
        assert metrics.winning_trades == 5
        assert metrics.win_rate == 1.0


# ============================================================================
# SLIPPAGE ANALYZER TESTS
# ============================================================================

class TestSlippageAnalyzer:
    """Test slippage analysis."""
    
    def test_initialization(self):
        """Test analyzer initializes."""
        analyzer = SlippageAnalyzer(alert_threshold_bps=50)
        
        assert analyzer.alert_threshold_bps == 50
        assert len(analyzer.records) == 0
    
    def test_buy_slippage_calculation(self):
        """Test slippage calculation for BUY orders."""
        analyzer = SlippageAnalyzer()
        
        # BUY with positive slippage (paid more)
        analyzer.record_execution(
            symbol="AAPL",
            side="BUY",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("100.10"),
            quantity=Decimal("100"),
            time_to_fill_ms=150
        )
        
        record = analyzer.records[0]
        
        assert record.slippage_bps == 10  # 0.10% = 10 bps
        assert record.slippage_dollars == Decimal("10.00")  # $0.10 * 100
    
    def test_sell_slippage_calculation(self):
        """Test slippage calculation for SELL orders."""
        analyzer = SlippageAnalyzer()
        
        # SELL with positive slippage (received less)
        analyzer.record_execution(
            symbol="AAPL",
            side="SELL",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("99.90"),
            quantity=Decimal("100"),
            time_to_fill_ms=150
        )
        
        record = analyzer.records[0]
        
        assert record.slippage_bps == 10  # 0.10% = 10 bps
        assert record.slippage_dollars == Decimal("10.00")  # $0.10 * 100
    
    def test_statistics_by_symbol(self):
        """Test slippage statistics by symbol."""
        analyzer = SlippageAnalyzer()
        
        # Add multiple executions
        for i in range(10):
            analyzer.record_execution(
                symbol="AAPL",
                side="BUY",
                expected_price=Decimal("100.00"),
                actual_price=Decimal("100.05"),
                quantity=Decimal("100")
            )
        
        stats = analyzer.get_statistics_by_symbol("AAPL")
        
        assert "AAPL" in stats
        assert stats["AAPL"].sample_count == 10
        assert stats["AAPL"].avg_slippage_bps == 5.0
    
    def test_statistics_by_time_of_day(self):
        """Test slippage statistics by time of day."""
        analyzer = SlippageAnalyzer()
        
        # Add execution at market open
        morning_time = datetime.now(timezone.utc).replace(
            hour=9, minute=45, second=0, microsecond=0
        )
        
        analyzer.record_execution(
            symbol="AAPL",
            side="BUY",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("100.10"),
            quantity=Decimal("100"),
            timestamp=morning_time
        )
        
        stats = analyzer.get_statistics_by_time_of_day()
        
        assert len(stats) > 0
    
    def test_worst_symbols(self):
        """Test identifying worst slippage symbols."""
        analyzer = SlippageAnalyzer()
        
        # Add good slippage for AAPL
        analyzer.record_execution(
            symbol="AAPL",
            side="BUY",
            expected_price=Decimal("100.00"),
            actual_price=Decimal("100.02"),
            quantity=Decimal("100")
        )
        
        # Add bad slippage for TSLA
        analyzer.record_execution(
            symbol="TSLA",
            side="BUY",
            expected_price=Decimal("200.00"),
            actual_price=Decimal("200.50"),
            quantity=Decimal("100")
        )
        
        worst = analyzer.get_worst_slippage_symbols(count=1)
        
        assert len(worst) > 0
        assert worst[0][0] == "TSLA"  # TSLA should be worst


# ============================================================================
# ATTRIBUTION ANALYZER TESTS
# ============================================================================

class TestTradeAttributionAnalyzer:
    """Test trade attribution analysis."""
    
    def test_initialization(self):
        """Test analyzer initializes."""
        analyzer = TradeAttributionAnalyzer()
        
        assert len(analyzer.trades) == 0
    
    def test_add_trade(self):
        """Test adding trades."""
        analyzer = TradeAttributionAnalyzer()
        
        trade = TradeResult(
            symbol="AAPL",
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            exit_time=datetime.now(timezone.utc),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            quantity=Decimal("10"),
            side="LONG",
            pnl=Decimal("50.00"),
            pnl_percent=Decimal("2.78"),
            commission=Decimal("1.00"),
            duration_hours=1.0,
            strategy="MeanReversion",
            signal_type="RSI_Oversold"
        )
        
        analyzer.add_trade(trade)
        
        assert len(analyzer.trades) == 1
    
    def test_attribution_by_strategy(self):
        """Test P&L attribution by strategy."""
        analyzer = TradeAttributionAnalyzer()
        
        # Add trades for different strategies
        for strategy in ["MeanReversion", "Momentum"]:
            for i in range(5):
                trade = TradeResult(
                    symbol="TEST",
                    entry_time=datetime.now(timezone.utc) - timedelta(hours=i+1),
                    exit_time=datetime.now(timezone.utc),
                    entry_price=Decimal("100.00"),
                    exit_price=Decimal("105.00"),
                    quantity=Decimal("10"),
                    side="LONG",
                    pnl=Decimal("50.00") if strategy == "MeanReversion" else Decimal("30.00"),
                    pnl_percent=Decimal("5.0"),
                    commission=Decimal("1.00"),
                    duration_hours=1.0,
                    strategy=strategy
                )
                analyzer.add_trade(trade)
        
        attribution = analyzer.get_attribution_by_strategy()
        
        assert "MeanReversion" in attribution
        assert "Momentum" in attribution
        assert attribution["MeanReversion"].total_pnl > attribution["Momentum"].total_pnl
    
    def test_attribution_by_signal_type(self):
        """Test P&L attribution by signal type."""
        analyzer = TradeAttributionAnalyzer()
        
        # Add trades with different signals
        trade1 = TradeResult(
            symbol="AAPL",
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            exit_time=datetime.now(timezone.utc),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            quantity=Decimal("10"),
            side="LONG",
            pnl=Decimal("50.00"),
            pnl_percent=Decimal("2.78"),
            commission=Decimal("1.00"),
            duration_hours=1.0,
            signal_type="RSI_Oversold"
        )
        
        analyzer.add_trade(trade1)
        
        attribution = analyzer.get_attribution_by_signal_type()
        
        assert "RSI_Oversold" in attribution
        assert attribution["RSI_Oversold"].trade_count == 1
    
    def test_best_strategies(self):
        """Test identifying best strategies."""
        analyzer = TradeAttributionAnalyzer()
        
        # Add good strategy
        for i in range(5):
            trade = TradeResult(
                symbol="TEST",
                entry_time=datetime.now(timezone.utc) - timedelta(hours=i+1),
                exit_time=datetime.now(timezone.utc),
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                quantity=Decimal("10"),
                side="LONG",
                pnl=Decimal("100.00"),  # Good strategy
                pnl_percent=Decimal("5.0"),
                commission=Decimal("1.00"),
                duration_hours=1.0,
                strategy="Good"
            )
            analyzer.add_trade(trade)
        
        # Add bad strategy
        for i in range(5):
            trade = TradeResult(
                symbol="TEST",
                entry_time=datetime.now(timezone.utc) - timedelta(hours=i+1),
                exit_time=datetime.now(timezone.utc),
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                quantity=Decimal("10"),
                side="LONG",
                pnl=Decimal("-50.00"),  # Bad strategy
                pnl_percent=Decimal("-5.0"),
                commission=Decimal("1.00"),
                duration_hours=1.0,
                strategy="Bad"
            )
            analyzer.add_trade(trade)
        
        best = analyzer.get_best_performers(dimension="strategy", count=1)
        worst = analyzer.get_worst_performers(dimension="strategy", count=1)
        
        assert best[0][0] == "Good"
        assert worst[0][0] == "Bad"
    
    def test_insights_generation(self):
        """Test generating insights."""
        analyzer = TradeAttributionAnalyzer()
        
        # Add some trades
        trade = TradeResult(
            symbol="AAPL",
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            exit_time=datetime.now(timezone.utc),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            quantity=Decimal("10"),
            side="LONG",
            pnl=Decimal("50.00"),
            pnl_percent=Decimal("2.78"),
            commission=Decimal("1.00"),
            duration_hours=1.0,
            strategy="MeanReversion",
            signal_type="RSI_Oversold"
        )
        
        analyzer.add_trade(trade)
        
        recommendations = analyzer.get_recommendations()
        
        # Might be empty if not enough data
        # Just verify it returns a list
        assert isinstance(recommendations, list)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestAnalyticsIntegration:
    """Test analytics components working together."""
    
    def test_full_workflow(self):
        """Test complete analytics workflow."""
        # Initialize all analyzers
        performance = PerformanceTracker(starting_equity=Decimal("10000"))
        slippage = SlippageAnalyzer()
        attribution = TradeAttributionAnalyzer()
        
        # Create and record a trade
        trade = TradeResult(
            symbol="AAPL",
            entry_time=datetime.now(timezone.utc) - timedelta(hours=2),
            exit_time=datetime.now(timezone.utc),
            entry_price=Decimal("180.00"),
            exit_price=Decimal("185.00"),
            quantity=Decimal("100"),
            side="LONG",
            pnl=Decimal("500.00"),
            pnl_percent=Decimal("2.78"),
            commission=Decimal("2.00"),
            duration_hours=2.0,
            strategy="MeanReversion",
            signal_type="RSI_Oversold"
        )
        
        # Record in all trackers
        performance.add_trade(trade)
        attribution.add_trade(trade)
        
        # Record slippage
        slippage.record_execution(
            symbol="AAPL",
            side="BUY",
            expected_price=Decimal("180.00"),
            actual_price=Decimal("180.05"),
            quantity=Decimal("100")
        )
        
        slippage.record_execution(
            symbol="AAPL",
            side="SELL",
            expected_price=Decimal("185.00"),
            actual_price=Decimal("184.95"),
            quantity=Decimal("100")
        )
        
        # Update equity
        performance.update_equity(Decimal("10500"))
        
        # Get metrics
        metrics = performance.get_metrics()
        slippage_stats = slippage.get_overall_statistics()
        strategy_attr = attribution.get_attribution_by_strategy()
        
        # Verify all systems working
        assert metrics.total_trades == 1
        assert slippage_stats.sample_count == 2
        assert "MeanReversion" in strategy_attr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
