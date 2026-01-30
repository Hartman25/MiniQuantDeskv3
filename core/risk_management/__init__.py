"""
Advanced Risk Management System (+8 Safety Points)

COMPREHENSIVE RISK MANAGEMENT:
- Dynamic position sizing (volatility-adjusted)
- Correlation tracking and limits
- Intraday drawdown monitoring
- Portfolio concentration detection
- Integrated risk orchestration

DESIGN STANDARDS:
- Van Tharp position sizing principles
- Modern portfolio theory (Markowitz)
- Bridgewater risk parity concepts
- Institutional risk management

USAGE:
    from core.risk_management import (
        RiskManager,
        SizingMethod,
        DrawdownStatus,
        RiskCheckStatus
    )
    
    # Initialize risk manager
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
        # Execute trade
        place_order(symbol, result.suggested_size)
    else:
        # Rejected
        logger.warning(f"Position rejected: {result.reasons}")
    
    # Monitor throughout day
    risk_mgr.update_equity(current_equity)
    
    if risk_mgr.is_trading_halted():
        trading_engine.halt("Risk limits exceeded")
    
    # Daily reset
    risk_mgr.reset_daily(new_starting_equity)
"""

# ============================================================================
# MASTER RISK MANAGER
# ============================================================================

from core.risk_management.manager import (
    RiskManager,
    RiskCheckStatus,
    RiskCheckResult
)

# ============================================================================
# POSITION SIZING
# ============================================================================

from core.risk_management.position_sizing import (
    DynamicPositionSizer,
    PositionSizeResult,
    SizingMethod
)

# ============================================================================
# CORRELATION TRACKING
# ============================================================================

from core.risk_management.correlation import (
    CorrelationMatrix,
    CorrelationPair,
    CorrelationCluster
)

# ============================================================================
# DRAWDOWN MONITORING
# ============================================================================

from core.risk_management.drawdown import (
    IntradayDrawdownMonitor,
    DrawdownStatus,
    DrawdownEvent
)

# ============================================================================
# PORTFOLIO HEAT MAPPING
# ============================================================================

from core.risk_management.heat_map import (
    PortfolioHeatMapper,
    ConcentrationAlert,
    PositionHeat,
    Sector
)


from core.risk_management.trailing_stops import (
    TrailingStopManager,
    TrailingStopState,
    StopTrigger,
    StopCheck
)

from core.risk_management.anti_pyramiding import (
    AntiPyramidingGuardian,
    PyramidingDecision,
    PyramidingCheck,
    PositionState
)


__all__ = [
    # Master risk manager
    "RiskManager",
    "RiskCheckStatus",
    "RiskCheckResult",
    
    # Position sizing
    "DynamicPositionSizer",
    "PositionSizeResult",
    "SizingMethod",
    
    # Correlation
    "CorrelationMatrix",
    "CorrelationPair",
    "CorrelationCluster",
    
    # Drawdown
    "IntradayDrawdownMonitor",
    "DrawdownStatus",
    "DrawdownEvent",
    
    # Heat mapping
    "PortfolioHeatMapper",
    "ConcentrationAlert",
    "PositionHeat",
    "Sector",
    
    # Trailing stops
    "TrailingStopManager",
    "TrailingStopState",
    "StopTrigger",
    "StopCheck",
    
    # Anti-pyramiding
    "AntiPyramidingGuardian",
    "PyramidingDecision",
    "PyramidingCheck",
    "PositionState",
]
