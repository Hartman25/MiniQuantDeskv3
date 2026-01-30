"""
Master risk manager orchestrating all risk components.

ARCHITECTURE:
- Unified risk management interface
- Orchestrates all risk subsystems
- Pre-trade risk checks
- Real-time risk monitoring
- Comprehensive risk reporting

RESPONSIBILITIES:
- Position sizing (volatility-adjusted)
- Correlation limit enforcement
- Drawdown monitoring
- Concentration detection
- Risk limit aggregation

This is the single entry point for all risk management.

Based on institutional risk management systems.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from enum import Enum

from core.risk_management.position_sizing import (
    DynamicPositionSizer,
    PositionSizeResult,
    SizingMethod
)
from core.risk_management.correlation import (
    CorrelationMatrix,
    CorrelationCluster
)
from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus
)
from core.risk_management.heat_map import (
    PortfolioHeatMapper,
    ConcentrationAlert,
    PositionHeat
)
from core.logging import get_logger, LogStream


# ============================================================================
# RISK CHECK RESULT
# ============================================================================

class RiskCheckStatus(Enum):
    """Risk check status."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WARNING = "WARNING"


@dataclass
class RiskCheckResult:
    """Result of pre-trade risk check."""
    status: RiskCheckStatus
    approved: bool
    reasons: List[str]
    warnings: List[str]
    suggested_size: Optional[int]
    max_allowed_size: Optional[int]
    metadata: Dict
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "approved": self.approved,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "suggested_size": self.suggested_size,
            "max_allowed_size": self.max_allowed_size,
            "metadata": self.metadata
        }


# ============================================================================
# MASTER RISK MANAGER
# ============================================================================

class RiskManager:
    """
    Master risk manager orchestrating all risk subsystems.
    
    RESPONSIBILITIES:
    - Pre-trade risk checks (approve/reject)
    - Position sizing (volatility-adjusted)
    - Correlation limit enforcement
    - Drawdown monitoring and halts
    - Concentration detection
    - Risk reporting and statistics
    
    COMPONENTS:
    - DynamicPositionSizer: Calculate position sizes
    - CorrelationMatrix: Track correlations
    - IntradayDrawdownMonitor: Monitor drawdown
    - PortfolioHeatMapper: Track concentration
    
    USAGE:
        # Initialize with account state
        risk_mgr = RiskManager(
            account_equity=Decimal("10000"),
            risk_per_trade_percent=Decimal("1.0"),
            max_position_percent=Decimal("10.0")
        )
        
        # Pre-trade check
        result = risk_mgr.check_new_position(
            symbol="AAPL",
            current_price=Decimal("185.00"),
            atr=Decimal("3.50")
        )
        
        if result.approved:
            # Place order for result.suggested_size shares
            place_order(symbol, result.suggested_size)
        else:
            # Rejected
            logger.warning(f"Position rejected: {result.reasons}")
        
        # Update equity throughout day
        risk_mgr.update_equity(Decimal("10150"))
        
        # Check if trading should halt
        if risk_mgr.is_trading_halted():
            trading_engine.halt("Risk limits exceeded")
    """
    
    def __init__(
        self,
        account_equity: Decimal,
        risk_per_trade_percent: Decimal = Decimal("1.0"),
        max_position_percent: Decimal = Decimal("10.0"),
        max_correlated_exposure_percent: Decimal = Decimal("25.0"),
        drawdown_warning_percent: Decimal = Decimal("5.0"),
        drawdown_halt_percent: Decimal = Decimal("10.0"),
        concentration_single_percent: Decimal = Decimal("20.0"),
        concentration_sector_percent: Decimal = Decimal("40.0"),
        sizing_method: SizingMethod = SizingMethod.VOLATILITY_ADJUSTED
    ):
        """
        Initialize risk manager.
        
        Args:
            account_equity: Current account equity
            risk_per_trade_percent: % to risk per trade (e.g., 1.0 = 1%)
            max_position_percent: Max % per position (e.g., 10.0 = 10%)
            max_correlated_exposure_percent: Max % for correlated group
            drawdown_warning_percent: % drawdown for warning
            drawdown_halt_percent: % drawdown to halt trading
            concentration_single_percent: % threshold for single position
            concentration_sector_percent: % threshold for sector
            sizing_method: Position sizing method
        """
        self.account_equity = account_equity
        
        self.logger = get_logger(LogStream.RISK)
        
        # Initialize subsystems
        self.position_sizer = DynamicPositionSizer(
            account_equity=account_equity,
            risk_per_trade_percent=risk_per_trade_percent,
            max_position_size_percent=max_position_percent,
            sizing_method=sizing_method
        )
        
        self.correlation_matrix = CorrelationMatrix(
            lookback_days=30,
            high_correlation_threshold=0.7,
            max_correlated_exposure_percent=max_correlated_exposure_percent
        )
        
        self.drawdown_monitor = IntradayDrawdownMonitor(
            starting_equity=account_equity,
            warning_threshold_percent=drawdown_warning_percent,
            halt_threshold_percent=drawdown_halt_percent
        )
        
        self.heatmap = PortfolioHeatMapper(
            account_equity=account_equity,
            concentration_threshold_single=concentration_single_percent,
            concentration_threshold_sector=concentration_sector_percent
        )
        
        # State
        self.current_positions: Dict[str, Decimal] = {}  # symbol -> exposure
        self.is_initialized = True
        
        self.logger.info("RiskManager initialized", extra={
            "account_equity": str(account_equity),
            "risk_per_trade": str(risk_per_trade_percent),
            "sizing_method": sizing_method.value
        })
    
    # ========================================================================
    # PRE-TRADE RISK CHECKS
    # ========================================================================
    
    def check_new_position(
        self,
        symbol: str,
        current_price: Decimal,
        atr: Optional[Decimal] = None,
        side: str = "BUY"
    ) -> RiskCheckResult:
        """
        Comprehensive pre-trade risk check.
        
        Checks:
        1. Trading not halted (drawdown)
        2. Position sizing constraints
        3. Correlation limits
        4. Concentration limits
        5. Available buying power
        
        Args:
            symbol: Stock symbol
            current_price: Current market price
            atr: Average True Range (for volatility adjustment)
            side: Order side (BUY/SELL)
            
        Returns:
            RiskCheckResult with approval/rejection and reasons
        """
        reasons = []
        warnings = []
        metadata = {}
        
        # Check 1: Trading halt (drawdown)
        if self.is_trading_halted():
            return RiskCheckResult(
                status=RiskCheckStatus.REJECTED,
                approved=False,
                reasons=["Trading halted due to drawdown limit"],
                warnings=[],
                suggested_size=None,
                max_allowed_size=None,
                metadata={"drawdown_pct": str(self.drawdown_monitor.get_drawdown_percent())}
            )
        
        # Check 2: Calculate position size
        try:
            size_result = self.position_sizer.calculate_size(
                symbol=symbol,
                current_price=current_price,
                atr=atr
            )
            
            suggested_size = size_result.suggested_shares
            max_size = size_result.max_shares
            
            metadata["sizing"] = size_result.to_dict()
            
            # Check if constrained
            if size_result.is_constrained():
                warnings.extend([
                    f"Size constrained: {', '.join(size_result.constraints_applied)}"
                ])
        
        except Exception as e:
            return RiskCheckResult(
                status=RiskCheckStatus.REJECTED,
                approved=False,
                reasons=[f"Position sizing failed: {str(e)}"],
                warnings=[],
                suggested_size=None,
                max_allowed_size=None,
                metadata={}
            )
        
        # Check 3: Correlation limits
        new_exposure = current_price * Decimal(str(suggested_size))
        
        allowed, corr_reason = self.correlation_matrix.check_correlated_exposure(
            symbol=symbol,
            new_exposure=new_exposure,
            current_positions=self.current_positions,
            account_equity=self.account_equity
        )
        
        if not allowed:
            return RiskCheckResult(
                status=RiskCheckStatus.REJECTED,
                approved=False,
                reasons=[corr_reason],
                warnings=warnings,
                suggested_size=None,
                max_allowed_size=None,
                metadata=metadata
            )
        
        # Check 4: Concentration limits
        # Simulate adding position
        test_positions = self.current_positions.copy()
        test_positions[symbol] = test_positions.get(symbol, Decimal("0")) + new_exposure
        
        test_heatmap = PortfolioHeatMapper(
            account_equity=self.account_equity,
            concentration_threshold_single=self.heatmap.threshold_single,
            concentration_threshold_sector=self.heatmap.threshold_sector
        )
        
        for sym, exp in test_positions.items():
            test_heatmap.update_position(sym, exp, exp * Decimal("0.02"))  # Simplified risk
        
        if test_heatmap.is_concentrated():
            concentrated = test_heatmap.get_concentrated_risks()
            # Check if new symbol causes concentration
            new_concentrations = [c for c in concentrated if symbol in c.symbols]
            
            if new_concentrations:
                return RiskCheckResult(
                    status=RiskCheckStatus.REJECTED,
                    approved=False,
                    reasons=[f"Would exceed concentration limits: {new_concentrations[0].category}"],
                    warnings=warnings,
                    suggested_size=None,
                    max_allowed_size=None,
                    metadata=metadata
                )
        
        # Check 5: Minimum size
        if suggested_size < 1:
            return RiskCheckResult(
                status=RiskCheckStatus.REJECTED,
                approved=False,
                reasons=["Calculated size < 1 share"],
                warnings=warnings,
                suggested_size=None,
                max_allowed_size=None,
                metadata=metadata
            )
        
        # All checks passed
        status = RiskCheckStatus.WARNING if warnings else RiskCheckStatus.APPROVED
        
        return RiskCheckResult(
            status=status,
            approved=True,
            reasons=[],
            warnings=warnings,
            suggested_size=suggested_size,
            max_allowed_size=max_size,
            metadata=metadata
        )
    
    # ========================================================================
    # POSITION MANAGEMENT
    # ========================================================================
    
    def add_position(
        self,
        symbol: str,
        exposure_dollars: Decimal,
        risk_dollars: Decimal
    ):
        """Add or update position in tracking."""
        self.current_positions[symbol] = exposure_dollars
        
        # Update heatmap
        self.heatmap.update_position(symbol, exposure_dollars, risk_dollars)
        
        self.logger.debug(f"Added position: {symbol}", extra={
            "symbol": symbol,
            "exposure": str(exposure_dollars),
            "risk": str(risk_dollars)
        })
    
    def remove_position(self, symbol: str):
        """Remove position from tracking."""
        if symbol in self.current_positions:
            del self.current_positions[symbol]
        
        self.heatmap.remove_position(symbol)
        
        self.logger.debug(f"Removed position: {symbol}")
    
    def update_position(
        self,
        symbol: str,
        new_exposure: Decimal,
        new_risk: Decimal
    ):
        """Update existing position."""
        self.current_positions[symbol] = new_exposure
        self.heatmap.update_position(symbol, new_exposure, new_risk)
    
    # ========================================================================
    # EQUITY UPDATES
    # ========================================================================
    
    def update_equity(self, new_equity: Decimal):
        """
        Update account equity (call frequently - every minute).
        
        Updates all subsystems and monitors drawdown.
        """
        old_equity = self.account_equity
        self.account_equity = new_equity
        
        # Update subsystems
        self.position_sizer.update_account_equity(new_equity)
        self.heatmap.update_account_equity(new_equity)
        
        # Monitor drawdown
        status = self.drawdown_monitor.update_equity(new_equity)
        
        if status == DrawdownStatus.HALT:
            self.logger.error(
                "🛑 TRADING HALTED - Drawdown limit exceeded",
                extra=self.drawdown_monitor.get_statistics()
            )
        elif status == DrawdownStatus.WARNING:
            self.logger.warning(
                "⚠️ Drawdown warning",
                extra=self.drawdown_monitor.get_statistics()
            )
        
        self.logger.debug("Updated equity", extra={
            "old_equity": str(old_equity),
            "new_equity": str(new_equity),
            "drawdown_status": status.value
        })
    
    def update_returns(self, symbol: str, return_pct: Decimal):
        """Update daily returns for correlation tracking."""
        self.correlation_matrix.update_returns(symbol, return_pct)
    
    # ========================================================================
    # RISK STATUS QUERIES
    # ========================================================================
    
    def is_trading_halted(self) -> bool:
        """Check if trading should be halted."""
        return self.drawdown_monitor.is_trading_halted()
    
    def get_drawdown_status(self) -> DrawdownStatus:
        """Get current drawdown status."""
        return self.drawdown_monitor.get_status()
    
    def get_risk_concentrations(self) -> List[ConcentrationAlert]:
        """Get all risk concentrations."""
        return self.heatmap.get_concentrations()
    
    def get_correlation_clusters(self) -> List[CorrelationCluster]:
        """Get correlation clusters."""
        return self.correlation_matrix.find_clusters(
            symbols=list(self.current_positions.keys()),
            positions=self.current_positions
        )
    
    def get_position_heatmap(self) -> List[PositionHeat]:
        """Get position heat map."""
        return self.heatmap.get_position_heatmap()
    
    # ========================================================================
    # DAILY OPERATIONS
    # ========================================================================
    
    def reset_daily(self, new_starting_equity: Decimal):
        """Reset for new trading day."""
        self.logger.info("Resetting risk manager for new day", extra={
            "new_starting_equity": str(new_starting_equity)
        })
        
        # Reset drawdown monitor
        self.drawdown_monitor.reset_daily(new_starting_equity)
        
        # Update equity in all subsystems
        self.update_equity(new_starting_equity)
    
    # ========================================================================
    # COMPREHENSIVE REPORTING
    # ========================================================================
    
    def get_risk_report(self) -> Dict:
        """
        Get comprehensive risk report.
        
        Returns:
            Dictionary with all risk metrics and status
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account_equity": str(self.account_equity),
            
            # Drawdown
            "drawdown": {
                "status": self.drawdown_monitor.get_status().value,
                "current_pct": str(self.drawdown_monitor.get_drawdown_percent()),
                "max_today_pct": str(self.drawdown_monitor.get_max_drawdown_today()),
                "is_halted": self.is_trading_halted(),
                **self.drawdown_monitor.get_statistics()
            },
            
            # Positions
            "positions": {
                "count": len(self.current_positions),
                "total_exposure": str(sum(self.current_positions.values())),
                "heatmap": [h.to_dict() for h in self.get_position_heatmap()[:5]],  # Top 5
            },
            
            # Concentration
            "concentration": {
                "is_concentrated": self.heatmap.is_concentrated(),
                "risks": [c.to_dict() for c in self.heatmap.get_concentrated_risks()],
                "sector_exposure": {
                    sector.value: str(pct)
                    for sector, pct in self.heatmap.get_sector_exposure().items()
                },
                **self.heatmap.get_statistics()
            },
            
            # Correlation
            "correlation": {
                "clusters": [c.to_dict() for c in self.get_correlation_clusters()],
                "diversification_score": round(
                    self.correlation_matrix.calculate_diversification_score(
                        self.current_positions
                    ), 3
                )
            }
        }
