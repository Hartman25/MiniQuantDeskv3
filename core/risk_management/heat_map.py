"""
Portfolio heat map and risk concentration analysis.

ARCHITECTURE:
- Visual risk concentration mapping
- Sector exposure tracking
- Position size heatmap
- Risk attribution by position
- Concentration alerts

DESIGN PRINCIPLE:
Make risk visible and actionable.

EXAMPLE:
Portfolio Heat Map:
  Tech (65%): AAPL(20%), MSFT(20%), NVDA(15%), GOOGL(10%)
  Finance (20%): JPM(10%), BAC(10%)
  Energy (15%): XOM(15%)

Alert: Tech sector over 50% limit!

Based on institutional portfolio management and risk visualization.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set
from collections import defaultdict
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# SECTOR DEFINITIONS
# ============================================================================

class Sector(Enum):
    """Market sectors."""
    TECHNOLOGY = "Technology"
    FINANCIALS = "Financials"
    HEALTHCARE = "Healthcare"
    CONSUMER_CYCLICAL = "Consumer Cyclical"
    CONSUMER_DEFENSIVE = "Consumer Defensive"
    INDUSTRIALS = "Industrials"
    ENERGY = "Energy"
    UTILITIES = "Utilities"
    REAL_ESTATE = "Real Estate"
    MATERIALS = "Materials"
    COMMUNICATIONS = "Communications"
    UNKNOWN = "Unknown"


# Simplified sector mapping (in production, use API lookup)
SYMBOL_TO_SECTOR: Dict[str, Sector] = {
    # Technology
    "AAPL": Sector.TECHNOLOGY, "MSFT": Sector.TECHNOLOGY, "NVDA": Sector.TECHNOLOGY,
    "GOOGL": Sector.TECHNOLOGY, "META": Sector.TECHNOLOGY, "TSLA": Sector.TECHNOLOGY,
    "AMD": Sector.TECHNOLOGY, "INTC": Sector.TECHNOLOGY, "CRM": Sector.TECHNOLOGY,
    
    # Financials
    "JPM": Sector.FINANCIALS, "BAC": Sector.FINANCIALS, "WFC": Sector.FINANCIALS,
    "GS": Sector.FINANCIALS, "MS": Sector.FINANCIALS, "C": Sector.FINANCIALS,
    
    # Healthcare
    "JNJ": Sector.HEALTHCARE, "UNH": Sector.HEALTHCARE, "PFE": Sector.HEALTHCARE,
    "ABBV": Sector.HEALTHCARE, "TMO": Sector.HEALTHCARE, "MRK": Sector.HEALTHCARE,
    
    # Energy
    "XOM": Sector.ENERGY, "CVX": Sector.ENERGY, "COP": Sector.ENERGY,
    
    # Consumer
    "AMZN": Sector.CONSUMER_CYCLICAL, "HD": Sector.CONSUMER_CYCLICAL,
    "WMT": Sector.CONSUMER_DEFENSIVE, "PG": Sector.CONSUMER_DEFENSIVE,
    
    # Index ETFs
    "SPY": Sector.UNKNOWN, "QQQ": Sector.TECHNOLOGY, "DIA": Sector.UNKNOWN,
    "IWM": Sector.UNKNOWN, "VTI": Sector.UNKNOWN,
}


# ============================================================================
# HEAT MAP DATA
# ============================================================================

@dataclass
class PositionHeat:
    """Heat data for a single position."""
    symbol: str
    sector: Sector
    notional: Decimal
    percent_of_portfolio: Decimal
    risk_contribution: Decimal  # Contribution to portfolio risk
    heat_score: float  # 0-1 (1=high risk)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "sector": self.sector.value,
            "notional": str(self.notional),
            "percent_of_portfolio": str(self.percent_of_portfolio),
            "risk_contribution": str(self.risk_contribution),
            "heat_score": round(self.heat_score, 3)
        }


@dataclass
class SectorExposure:
    """Exposure for a sector."""
    sector: Sector
    symbols: List[str]
    total_notional: Decimal
    percent_of_portfolio: Decimal
    position_count: int
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "sector": self.sector.value,
            "symbols": self.symbols,
            "total_notional": str(self.total_notional),
            "percent_of_portfolio": str(self.percent_of_portfolio),
            "position_count": self.position_count
        }


@dataclass
class ConcentrationAlert:
    """Risk concentration alert."""
    alert_type: str  # "sector", "position", "correlation"
    severity: str  # "WARNING", "CRITICAL"
    message: str
    details: Dict
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


# ============================================================================
# PORTFOLIO HEAT MAPPER
# ============================================================================

class PortfolioHeatMapper:
    """
    Portfolio risk concentration and visualization.
    
    RESPONSIBILITIES:
    - Calculate position heat scores
    - Track sector exposure
    - Detect concentration risks
    - Generate risk attribution
    - Alert on over-concentration
    
    HEAT SCORE CALCULATION:
    - Size: % of portfolio
    - Volatility: ATR relative to price
    - Concentration: Sector/correlation clustering
    - Score: Weighted combination (0-1)
    
    USAGE:
        mapper = PortfolioHeatMapper(
            max_sector_exposure_percent=Decimal("30.0"),
            max_position_exposure_percent=Decimal("10.0")
        )
        
        # Update positions
        heat_map = mapper.calculate_heat_map(
            positions={
                "AAPL": Decimal("5000"),
                "MSFT": Decimal("4000"),
                "NVDA": Decimal("3000")
            },
            portfolio_value=Decimal("50000"),
            volatilities={"AAPL": 0.25, "MSFT": 0.22, "NVDA": 0.35}
        )
        
        # Check alerts
        alerts = mapper.get_concentration_alerts()
        for alert in alerts:
            if alert.severity == "CRITICAL":
                logger.error(alert.message)
    """
    
    def __init__(
        self,
        max_sector_exposure_percent: Decimal = Decimal("30.0"),
        max_position_exposure_percent: Decimal = Decimal("10.0"),
        sector_warning_threshold: Decimal = Decimal("25.0"),
        position_warning_threshold: Decimal = Decimal("8.0")
    ):
        """
        Initialize heat mapper.
        
        Args:
            max_sector_exposure_percent: Max % exposure to any sector
            max_position_exposure_percent: Max % exposure to any position
            sector_warning_threshold: Warning threshold for sector
            position_warning_threshold: Warning threshold for position
        """
        self.max_sector_exposure = max_sector_exposure_percent
        self.max_position_exposure = max_position_exposure_percent
        self.sector_warning_threshold = sector_warning_threshold
        self.position_warning_threshold = position_warning_threshold
        
        self.logger = get_logger(LogStream.RISK)
        
        # Current state
        self.current_heat_map: Dict[str, PositionHeat] = {}
        self.current_sector_exposure: Dict[Sector, SectorExposure] = {}
        self.current_alerts: List[ConcentrationAlert] = []
        
        self.logger.info("PortfolioHeatMapper initialized", extra={
            "max_sector_exposure": str(max_sector_exposure_percent),
            "max_position_exposure": str(max_position_exposure_percent)
        })
    
    # ========================================================================
    # HEAT MAP CALCULATION
    # ========================================================================
    
    def calculate_heat_map(
        self,
        positions: Dict[str, Decimal],
        portfolio_value: Decimal,
        volatilities: Optional[Dict[str, float]] = None,
        correlations: Optional[Dict[str, float]] = None
    ) -> Dict[str, PositionHeat]:
        """
        Calculate portfolio heat map.
        
        Args:
            positions: Position notional values
            portfolio_value: Total portfolio value
            volatilities: Symbol volatilities (annualized)
            correlations: Average correlation per symbol
            
        Returns:
            Dictionary of symbol -> PositionHeat
        """
        volatilities = volatilities or {}
        correlations = correlations or {}
        
        heat_map = {}
        
        for symbol, notional in positions.items():
            # Calculate percentage
            pct = (notional / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")
            
            # Get volatility (default to 0.25 if unknown)
            vol = volatilities.get(symbol, 0.25)
            
            # Get correlation (default to 0.5 if unknown)
            corr = correlations.get(symbol, 0.5)
            
            # Calculate risk contribution
            risk_contrib = notional * Decimal(str(vol))
            
            # Calculate heat score (0-1)
            heat_score = self._calculate_heat_score(
                pct=float(pct),
                volatility=vol,
                correlation=corr
            )
            
            # Get sector
            sector = self._get_sector(symbol)
            
            heat_map[symbol] = PositionHeat(
                symbol=symbol,
                sector=sector,
                notional=notional,
                percent_of_portfolio=pct,
                risk_contribution=risk_contrib,
                heat_score=heat_score
            )
        
        self.current_heat_map = heat_map
        
        return heat_map
    
    def _calculate_heat_score(
        self,
        pct: float,
        volatility: float,
        correlation: float
    ) -> float:
        """
        Calculate heat score (0-1).
        
        Factors:
        - Size: Larger positions = higher heat
        - Volatility: More volatile = higher heat
        - Correlation: More correlated = higher heat
        """
        # Normalize to 0-1 scales
        size_score = min(pct / 10.0, 1.0)  # 10% = max
        vol_score = min(volatility / 0.5, 1.0)  # 50% vol = max
        corr_score = abs(correlation)  # Already 0-1
        
        # Weighted combination
        heat = (
            0.5 * size_score +
            0.3 * vol_score +
            0.2 * corr_score
        )
        
        return min(heat, 1.0)
    
    # ========================================================================
    # SECTOR EXPOSURE
    # ========================================================================
    
    def calculate_sector_exposure(
        self,
        positions: Dict[str, Decimal],
        portfolio_value: Decimal
    ) -> Dict[Sector, SectorExposure]:
        """
        Calculate exposure by sector.
        
        Args:
            positions: Position notional values
            portfolio_value: Total portfolio value
            
        Returns:
            Dictionary of Sector -> SectorExposure
        """
        sector_data: Dict[Sector, Dict] = defaultdict(lambda: {
            "symbols": [],
            "total": Decimal("0"),
            "count": 0
        })
        
        for symbol, notional in positions.items():
            sector = self._get_sector(symbol)
            
            sector_data[sector]["symbols"].append(symbol)
            sector_data[sector]["total"] += notional
            sector_data[sector]["count"] += 1
        
        sector_exposure = {}
        
        for sector, data in sector_data.items():
            pct = (data["total"] / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")
            
            sector_exposure[sector] = SectorExposure(
                sector=sector,
                symbols=sorted(data["symbols"]),
                total_notional=data["total"],
                percent_of_portfolio=pct,
                position_count=data["count"]
            )
        
        self.current_sector_exposure = sector_exposure
        
        return sector_exposure
    
    def _get_sector(self, symbol: str) -> Sector:
        """Get sector for a symbol."""
        return SYMBOL_TO_SECTOR.get(symbol, Sector.UNKNOWN)
    
    # ========================================================================
    # CONCENTRATION ALERTS
    # ========================================================================
    
    def check_concentration(
        self,
        positions: Dict[str, Decimal],
        portfolio_value: Decimal
    ) -> List[ConcentrationAlert]:
        """
        Check for concentration risks.
        
        Args:
            positions: Position notional values
            portfolio_value: Total portfolio value
            
        Returns:
            List of concentration alerts
        """
        alerts = []
        
        # Check position concentration
        for symbol, notional in positions.items():
            pct = (notional / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")
            
            if pct >= self.max_position_exposure:
                alerts.append(ConcentrationAlert(
                    alert_type="position",
                    severity="CRITICAL",
                    message=f"{symbol} exceeds max position size",
                    details={
                        "symbol": symbol,
                        "percent": str(pct),
                        "max": str(self.max_position_exposure)
                    },
                    timestamp=datetime.now(timezone.utc)
                ))
            
            elif pct >= self.position_warning_threshold:
                alerts.append(ConcentrationAlert(
                    alert_type="position",
                    severity="WARNING",
                    message=f"{symbol} approaching max position size",
                    details={
                        "symbol": symbol,
                        "percent": str(pct),
                        "warning": str(self.position_warning_threshold)
                    },
                    timestamp=datetime.now(timezone.utc)
                ))
        
        # Check sector concentration
        sector_exposure = self.calculate_sector_exposure(positions, portfolio_value)
        
        for sector, exposure in sector_exposure.items():
            if sector == Sector.UNKNOWN:
                continue
            
            pct = exposure.percent_of_portfolio
            
            if pct >= self.max_sector_exposure:
                alerts.append(ConcentrationAlert(
                    alert_type="sector",
                    severity="CRITICAL",
                    message=f"{sector.value} sector exceeds max exposure",
                    details={
                        "sector": sector.value,
                        "symbols": exposure.symbols,
                        "percent": str(pct),
                        "max": str(self.max_sector_exposure)
                    },
                    timestamp=datetime.now(timezone.utc)
                ))
            
            elif pct >= self.sector_warning_threshold:
                alerts.append(ConcentrationAlert(
                    alert_type="sector",
                    severity="WARNING",
                    message=f"{sector.value} sector approaching max exposure",
                    details={
                        "sector": sector.value,
                        "symbols": exposure.symbols,
                        "percent": str(pct),
                        "warning": str(self.sector_warning_threshold)
                    },
                    timestamp=datetime.now(timezone.utc)
                ))
        
        self.current_alerts = alerts
        
        return alerts
    
    def get_concentration_alerts(self) -> List[ConcentrationAlert]:
        """Get current concentration alerts."""
        return self.current_alerts
    
    # ========================================================================
    # RISK ATTRIBUTION
    # ========================================================================
    
    def get_risk_attribution(self) -> List[PositionHeat]:
        """
        Get positions sorted by risk contribution.
        
        Returns:
            List of PositionHeat sorted by risk (highest first)
        """
        return sorted(
            self.current_heat_map.values(),
            key=lambda p: p.risk_contribution,
            reverse=True
        )
    
    def get_top_risks(self, count: int = 5) -> List[PositionHeat]:
        """Get top N riskiest positions."""
        return self.get_risk_attribution()[:count]
    
    # ========================================================================
    # VISUALIZATION DATA
    # ========================================================================
    
    def get_heat_map_data(self) -> Dict:
        """
        Get complete heat map data for visualization.
        
        Returns:
            Dictionary with positions, sectors, and alerts
        """
        return {
            "positions": {
                symbol: heat.to_dict()
                for symbol, heat in self.current_heat_map.items()
            },
            "sectors": {
                sector.value: exposure.to_dict()
                for sector, exposure in self.current_sector_exposure.items()
            },
            "alerts": [alert.to_dict() for alert in self.current_alerts],
            "summary": {
                "position_count": len(self.current_heat_map),
                "sector_count": len(self.current_sector_exposure),
                "alert_count": len(self.current_alerts),
                "critical_alerts": len([a for a in self.current_alerts if a.severity == "CRITICAL"])
            }
        }
