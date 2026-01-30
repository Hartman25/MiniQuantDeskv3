"""
Tests for risk management system.

COVERAGE:
- DynamicPositionSizer (volatility-adjusted sizing)
- CorrelationMatrix (correlation tracking)
- IntradayDrawdownMonitor (drawdown protection)
- PortfolioHeatMapper (concentration detection)
- RiskManager (integrated orchestration)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.risk_management import (
    RiskManager,
    RiskCheckStatus,
    DynamicPositionSizer,
    SizingMethod,
    CorrelationMatrix,
    IntradayDrawdownMonitor,
    DrawdownStatus,
    PortfolioHeatMapper,
    Sector
)


# ============================================================================
# POSITION SIZING TESTS
# ============================================================================

class TestDynamicPositionSizer:
    """Test dynamic position sizing."""
    
    def test_initialization(self):
        """Test sizer initializes correctly."""
        sizer = DynamicPositionSizer(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0"),
            max_position_size_percent=Decimal("10.0")
        )
        
        assert sizer.account_equity == Decimal("10000")
        assert sizer.risk_per_trade_dollars == Decimal("100")  # 1% of 10000
        assert sizer.max_position_notional == Decimal("1000")  # 10% of 10000
    
    def test_volatility_adjusted_sizing(self):
        """Test volatility-adjusted position sizing."""
        sizer = DynamicPositionSizer(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0"),
            max_position_size_percent=Decimal("50.0"),  # Higher to avoid constraints
            sizing_method=SizingMethod.VOLATILITY_ADJUSTED
        )
        
        # Low volatility stock (ATR=$2) → larger position
        result1 = sizer.calculate_size(
            symbol="SPY",
            current_price=Decimal("100.00"),
            atr=Decimal("2.00")
        )
        
        # High volatility stock (ATR=$10) → smaller position
        result2 = sizer.calculate_size(
            symbol="TSLA",
            current_price=Decimal("100.00"),  # Same price for fair comparison
            atr=Decimal("10.00")
        )
        
        # Low volatility should get more shares (5x less volatility = ~5x more shares)
        # ATR ratio: 10/2 = 5, so result1 should be ~5x result2
        assert result1.suggested_shares > result2.suggested_shares
        assert result1.suggested_shares >= result2.suggested_shares * 3  # Allow some variance
    
    def test_max_position_constraint(self):
        """Test maximum position size constraint."""
        sizer = DynamicPositionSizer(
            account_equity=Decimal("10000"),
            max_position_size_percent=Decimal("10.0")
        )
        
        result = sizer.calculate_size(
            symbol="AAPL",
            current_price=Decimal("100.00"),
            atr=Decimal("2.00")
        )
        
        # Max notional should be $1000 (10% of $10000)
        assert result.suggested_notional <= Decimal("1000")
        assert result.suggested_shares <= 10  # $1000 / $100
    
    def test_equity_update(self):
        """Test updating account equity."""
        sizer = DynamicPositionSizer(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0")
        )
        
        # Update equity
        sizer.update_account_equity(Decimal("15000"))
        
        assert sizer.account_equity == Decimal("15000")
        assert sizer.risk_per_trade_dollars == Decimal("150")  # 1% of 15000


# ============================================================================
# CORRELATION MATRIX TESTS
# ============================================================================

class TestCorrelationMatrix:
    """Test correlation matrix tracking."""
    
    def test_initialization(self):
        """Test correlation matrix initializes."""
        matrix = CorrelationMatrix(
            lookback_days=30,
            high_correlation_threshold=0.7
        )
        
        assert matrix.lookback_days == 30
        assert matrix.high_correlation_threshold == 0.7
    
    def test_returns_update(self):
        """Test updating returns."""
        matrix = CorrelationMatrix()
        
        matrix.update_returns("SPY", Decimal("1.0"))
        matrix.update_returns("SPY", Decimal("-0.5"))
        matrix.update_returns("SPY", Decimal("0.8"))
        
        assert "SPY" in matrix._returns
        assert len(matrix._returns["SPY"]) == 3
    
    def test_correlation_calculation(self):
        """Test correlation calculation."""
        matrix = CorrelationMatrix(min_correlation_samples=5)
        
        # Create perfectly correlated returns
        for i in range(30):
            return_val = Decimal(str(i % 10 - 5))  # -5 to 4
            matrix.update_returns("SPY", return_val)
            matrix.update_returns("QQQ", return_val)  # Same returns
        
        corr = matrix.get_correlation("SPY", "QQQ")
        
        assert corr is not None
        assert abs(corr - 1.0) < 0.01  # Should be close to 1.0
    
    def test_cluster_detection(self):
        """Test finding correlation clusters."""
        matrix = CorrelationMatrix(
            min_correlation_samples=10,
            high_correlation_threshold=0.8
        )
        
        # Create correlated returns for tech stocks
        for i in range(30):
            base_return = Decimal(str((i % 10 - 5) / 10))
            matrix.update_returns("AAPL", base_return)
            matrix.update_returns("MSFT", base_return + Decimal("0.01"))
            matrix.update_returns("GOOGL", base_return - Decimal("0.01"))
        
        clusters = matrix.find_clusters(["AAPL", "MSFT", "GOOGL"])
        
        # Should find one cluster with all three
        assert len(clusters) > 0
        assert len(clusters[0].symbols) >= 2


# ============================================================================
# DRAWDOWN MONITOR TESTS
# ============================================================================

class TestIntradayDrawdownMonitor:
    """Test intraday drawdown monitoring."""
    
    def test_initialization(self):
        """Test monitor initializes correctly."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0"),
            halt_threshold_percent=Decimal("10.0")
        )
        
        assert monitor.starting_equity == Decimal("10000")
        assert monitor.peak_equity == Decimal("10000")
        assert monitor.current_status == DrawdownStatus.NORMAL
    
    def test_normal_status(self):
        """Test normal status when no drawdown."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000")
        )
        
        status = monitor.update_equity(Decimal("10100"))
        
        assert status == DrawdownStatus.NORMAL
        assert monitor.peak_equity == Decimal("10100")  # New high
    
    def test_warning_status(self):
        """Test warning status on moderate drawdown."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            warning_threshold_percent=Decimal("5.0")
        )
        
        # Set peak
        monitor.update_equity(Decimal("10500"))
        
        # Draw down 6% from peak
        status = monitor.update_equity(Decimal("9870"))  # -6% from 10500
        
        assert status == DrawdownStatus.WARNING
    
    def test_halt_status(self):
        """Test halt status on excessive drawdown."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000"),
            halt_threshold_percent=Decimal("10.0")
        )
        
        # Set peak
        monitor.update_equity(Decimal("10500"))
        
        # Draw down 11% from peak
        status = monitor.update_equity(Decimal("9345"))  # -11% from 10500
        
        assert status == DrawdownStatus.HALT
        assert monitor.is_trading_halted()
    
    def test_daily_reset(self):
        """Test daily reset clears state."""
        monitor = IntradayDrawdownMonitor(
            starting_equity=Decimal("10000")
        )
        
        # Trigger warning
        monitor.update_equity(Decimal("9400"))
        
        # Reset for new day
        monitor.reset_daily(Decimal("9400"))
        
        assert monitor.current_status == DrawdownStatus.NORMAL
        assert monitor.max_drawdown_today == Decimal("0")


# ============================================================================
# HEAT MAPPER TESTS
# ============================================================================

class TestPortfolioHeatMapper:
    """Test portfolio heat mapping."""
    
    def test_initialization(self):
        """Test heat mapper initializes."""
        mapper = PortfolioHeatMapper(
            account_equity=Decimal("10000"),
            concentration_threshold_single=Decimal("20.0")
        )
        
        assert mapper.account_equity == Decimal("10000")
        assert mapper.threshold_single == Decimal("20.0")
    
    def test_position_tracking(self):
        """Test adding and tracking positions."""
        mapper = PortfolioHeatMapper(
            account_equity=Decimal("10000")
        )
        
        mapper.update_position("AAPL", Decimal("1000"), Decimal("50"))
        mapper.update_position("MSFT", Decimal("1500"), Decimal("75"))
        
        assert len(mapper._positions) == 2
    
    def test_sector_exposure(self):
        """Test sector exposure calculation."""
        mapper = PortfolioHeatMapper(
            account_equity=Decimal("10000")
        )
        
        mapper.update_position("AAPL", Decimal("2000"), Decimal("100"))  # Tech
        mapper.update_position("MSFT", Decimal("1500"), Decimal("75"))   # Tech
        mapper.update_position("JPM", Decimal("500"), Decimal("25"))     # Financials
        
        sector_exp = mapper.get_sector_exposure()
        
        assert Sector.TECHNOLOGY in sector_exp
        assert sector_exp[Sector.TECHNOLOGY] > sector_exp[Sector.FINANCIALS]
    
    def test_concentration_detection(self):
        """Test concentration detection."""
        mapper = PortfolioHeatMapper(
            account_equity=Decimal("10000"),
            concentration_threshold_single=Decimal("20.0")
        )
        
        # Add concentrated position (30%)
        mapper.update_position("AAPL", Decimal("3000"), Decimal("150"))
        
        assert mapper.is_concentrated()
        
        concentrations = mapper.get_concentrated_risks()
        assert len(concentrations) > 0
    
    def test_heatmap_generation(self):
        """Test heat map generation."""
        mapper = PortfolioHeatMapper(
            account_equity=Decimal("10000")
        )
        
        mapper.update_position("AAPL", Decimal("2000"), Decimal("100"))
        mapper.update_position("TSLA", Decimal("500"), Decimal("50"))
        
        heatmap = mapper.get_position_heatmap()
        
        assert len(heatmap) == 2
        # AAPL should have higher heat (larger exposure)
        assert heatmap[0].heat_score > heatmap[1].heat_score


# ============================================================================
# INTEGRATED RISK MANAGER TESTS
# ============================================================================

class TestRiskManager:
    """Test integrated risk manager."""
    
    def test_initialization(self):
        """Test risk manager initializes all subsystems."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0")
        )
        
        assert risk_mgr.account_equity == Decimal("10000")
        assert risk_mgr.position_sizer is not None
        assert risk_mgr.correlation_matrix is not None
        assert risk_mgr.drawdown_monitor is not None
        assert risk_mgr.heatmap is not None
    
    def test_approved_position_check(self):
        """Test position check approves valid position."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000")
        )
        
        result = risk_mgr.check_new_position(
            symbol="AAPL",
            current_price=Decimal("185.00"),
            atr=Decimal("3.50")
        )
        
        assert result.approved
        assert result.status in [RiskCheckStatus.APPROVED, RiskCheckStatus.WARNING]
        assert result.suggested_size is not None
        assert result.suggested_size > 0
    
    def test_rejected_during_halt(self):
        """Test position rejected when trading halted."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000"),
            drawdown_halt_percent=Decimal("10.0")
        )
        
        # Trigger halt
        risk_mgr.update_equity(Decimal("8500"))  # -15% drawdown
        
        result = risk_mgr.check_new_position(
            symbol="AAPL",
            current_price=Decimal("185.00"),
            atr=Decimal("3.50")
        )
        
        assert not result.approved
        assert result.status == RiskCheckStatus.REJECTED
        assert "halted" in result.reasons[0].lower()
    
    def test_position_management(self):
        """Test adding and removing positions."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000")
        )
        
        # Add position
        risk_mgr.add_position("AAPL", Decimal("1000"), Decimal("50"))
        
        assert "AAPL" in risk_mgr.current_positions
        
        # Remove position
        risk_mgr.remove_position("AAPL")
        
        assert "AAPL" not in risk_mgr.current_positions
    
    def test_equity_updates(self):
        """Test equity updates propagate to subsystems."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000")
        )
        
        # Update equity
        risk_mgr.update_equity(Decimal("11000"))
        
        assert risk_mgr.account_equity == Decimal("11000")
        assert risk_mgr.position_sizer.account_equity == Decimal("11000")
    
    def test_risk_report_generation(self):
        """Test comprehensive risk report."""
        risk_mgr = RiskManager(
            account_equity=Decimal("10000")
        )
        
        # Add some positions
        risk_mgr.add_position("AAPL", Decimal("1000"), Decimal("50"))
        risk_mgr.add_position("MSFT", Decimal("1500"), Decimal("75"))
        
        report = risk_mgr.get_risk_report()
        
        assert "drawdown" in report
        assert "positions" in report
        assert "concentration" in report
        assert "correlation" in report
        assert report["positions"]["count"] == 2


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestRiskManagementIntegration:
    """Test risk management components working together."""
    
    def test_full_workflow(self):
        """Test complete risk management workflow."""
        # Initialize
        risk_mgr = RiskManager(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0"),
            max_position_percent=Decimal("10.0")
        )
        
        # Check position 1
        result1 = risk_mgr.check_new_position(
            symbol="AAPL",
            current_price=Decimal("185.00"),
            atr=Decimal("3.50")
        )
        
        assert result1.approved
        
        # Add position
        risk_mgr.add_position(
            "AAPL",
            Decimal(str(result1.suggested_size)) * Decimal("185.00"),
            Decimal("50")
        )
        
        # Update returns for correlation
        risk_mgr.update_returns("AAPL", Decimal("1.0"))
        
        # Update equity
        risk_mgr.update_equity(Decimal("10100"))
        
        # Check not halted
        assert not risk_mgr.is_trading_halted()
        
        # Get report
        report = risk_mgr.get_risk_report()
        
        assert report is not None
        assert report["positions"]["count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
