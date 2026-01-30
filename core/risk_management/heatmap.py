"""
Portfolio heat mapping and risk concentration visualization.

ARCHITECTURE:
- Calculate risk concentration by symbol
- Calculate risk concentration by sector
- Identify overconcentration
- Risk attribution analysis
- Visual heatmap data generation

DESIGN PRINCIPLE:
Know where your risk is concentrated.

EXAMPLE:
- 5 tech positions: AAPL, MSFT, NVDA, GOOGL, META
- All correlated
- Tech sector: 60% of portfolio risk
- → OVERCONCENTRATED

This prevents sector blowups from destroying the portfolio.

Based on risk management at quantitative hedge funds.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# CONCENTRATION METRICS
# ============================================================================

class ConcentrationLevel(Enum):
    """Risk concentration level."""
    LOW = "LOW"  # <20% in any category
    MODERATE = "MODERATE"  # 20-40%
    HIGH = "HIGH"  # 40-60%
    EXTREME = "EXTREME"  # >60%


@dataclass
class RiskBucket:
    """Risk bucket (sector, symbol, or other grouping)."""
    name: str
    exposure: Decimal
    exposure_percent: Decimal
    risk_contribution: Decimal  # Volatility-adjusted risk
    risk_percent: Decimal
    position_count: int
    symbols: Set[str]
    concentration_level: ConcentrationLevel
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "exposure": str(self.exposure),
            "exposure_percent": str(self.exposure_percent),
            "risk_contribution": str(self.risk_contribution),
            "risk_percent": str(self.risk_percent),
            "position_count": self.position_count,
            "symbols": sorted(list(self.symbols)),
            "concentration_level": self.concentration_level.value
        }


@dataclass
class HeatmapData:
    """Heatmap visualization data."""
    timestamp: datetime
    by_symbol: List[RiskBucket]
    by_sector: List[RiskBucket]
    top_risks: List[Tuple[str, Decimal]]  # (name, risk_pct)
    diversification_score: float  # 0-1 (1=well diversified)
    max_concentration: Decimal
    concentration_warnings: List[str]
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "by_symbol": [b.to_dict() for b in self.by_symbol],
            "by_sector": [b.to_dict() for b in self.by_sector],
            "top_risks": [(name, str(pct)) for name, pct in self.top_risks],
            "diversification_score": round(self.diversification_score, 3),
            "max_concentration": str(self.max_concentration),
            "concentration_warnings": self.concentration_warnings
        }


# ============================================================================
# PORTFOLIO HEAT MAPPER
# ============================================================================

class PortfolioHeatMapper:
    """
    Portfolio risk concentration analyzer.
    
    RESPONSIBILITIES:
    - Calculate risk by symbol
    - Calculate risk by sector
    - Identify overconcentration
    - Generate heatmap data
    - Risk attribution analysis
    
    RISK CALCULATION:
    risk = exposure * volatility * correlation_factor
    
    This accounts for:
    - Position size
    - Volatility (some stocks are riskier)
    - Correlation (correlated positions = concentrated risk)
    
    USAGE:
        mapper = PortfolioHeatMapper(
            sector_map={"AAPL": "Technology", "SPY": "Index"},
            max_sector_concentration_percent=Decimal("40.0"),
            max_symbol_concentration_percent=Decimal("15.0")
        )
        
        # Calculate heatmap
        heatmap = mapper.calculate_heatmap(
            positions={
                "AAPL": Decimal("5000"),
                "MSFT": Decimal("4000"),
                "SPY": Decimal("3000")
            },
            volatilities={
                "AAPL": Decimal("0.25"),
                "MSFT": Decimal("0.22"),
                "SPY": Decimal("0.15")
            },
            correlations=correlation_matrix
        )
        
        # Check for warnings
        if heatmap.concentration_warnings:
            for warning in heatmap.concentration_warnings:
                logger.warning(warning)
    """
    
    def __init__(
        self,
        sector_map: Dict[str, str],
        max_sector_concentration_percent: Decimal = Decimal("40.0"),
        max_symbol_concentration_percent: Decimal = Decimal("15.0"),
        min_diversification_score: float = 0.5
    ):
        """
        Initialize heat mapper.
        
        Args:
            sector_map: Mapping of symbol -> sector
            max_sector_concentration_percent: Max % in any sector
            max_symbol_concentration_percent: Max % in any symbol
            min_diversification_score: Minimum diversification score
        """
        self.sector_map = sector_map
        self.max_sector_concentration = max_sector_concentration_percent
        self.max_symbol_concentration = max_symbol_concentration_percent
        self.min_diversification_score = min_diversification_score
        
        self.logger = get_logger(LogStream.RISK)
        
        self.logger.info("PortfolioHeatMapper initialized", extra={
            "sectors_tracked": len(set(sector_map.values())),
            "max_sector_concentration": str(max_sector_concentration_percent),
            "max_symbol_concentration": str(max_symbol_concentration_percent)
        })
    
    # ========================================================================
    # HEATMAP CALCULATION
    # ========================================================================
    
    def calculate_heatmap(
        self,
        positions: Dict[str, Decimal],
        volatilities: Dict[str, Decimal],
        correlations: Optional[Dict[Tuple[str, str], float]] = None,
        account_equity: Optional[Decimal] = None
    ) -> HeatmapData:
        """
        Calculate portfolio heatmap.
        
        Args:
            positions: Symbol -> exposure (dollars)
            volatilities: Symbol -> annualized volatility (e.g., 0.25 = 25%)
            correlations: (symbol1, symbol2) -> correlation
            account_equity: Total account equity (for percentages)
            
        Returns:
            HeatmapData with concentration analysis
        """
        if not positions:
            return self._empty_heatmap()
        
        # Calculate total exposure
        total_exposure = sum(positions.values())
        account_equity = account_equity or total_exposure
        
        # Calculate risk contributions
        risk_contributions = self._calculate_risk_contributions(
            positions, volatilities, correlations
        )
        
        total_risk = sum(risk_contributions.values())
        
        # Build symbol buckets
        by_symbol = self._build_symbol_buckets(
            positions, risk_contributions, total_exposure, total_risk, account_equity
        )
        
        # Build sector buckets
        by_sector = self._build_sector_buckets(
            positions, risk_contributions, total_exposure, total_risk, account_equity
        )
        
        # Identify top risks
        top_risks = sorted(
            [(sym, (risk / total_risk) * Decimal("100")) 
             for sym, risk in risk_contributions.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        # Calculate diversification score
        diversification_score = self._calculate_diversification_score(
            risk_contributions, correlations
        )
        
        # Find max concentration
        max_concentration = max(
            [b.risk_percent for b in by_symbol] + [b.risk_percent for b in by_sector],
            default=Decimal("0")
        )
        
        # Generate warnings
        warnings = self._generate_warnings(
            by_symbol, by_sector, diversification_score, max_concentration
        )
        
        heatmap = HeatmapData(
            timestamp=datetime.now(timezone.utc),
            by_symbol=by_symbol,
            by_sector=by_sector,
            top_risks=top_risks,
            diversification_score=diversification_score,
            max_concentration=max_concentration,
            concentration_warnings=warnings
        )
        
        if warnings:
            self.logger.warning(
                "Portfolio concentration warnings",
                extra={"warnings": warnings}
            )
        
        return heatmap
    
    # ========================================================================
    # RISK CONTRIBUTION CALCULATION
    # ========================================================================
    
    def _calculate_risk_contributions(
        self,
        positions: Dict[str, Decimal],
        volatilities: Dict[str, Decimal],
        correlations: Optional[Dict[Tuple[str, str], float]]
    ) -> Dict[str, Decimal]:
        """
        Calculate risk contribution for each position.
        
        Risk = exposure * volatility * correlation_factor
        
        Where correlation_factor accounts for how correlated
        this position is with the rest of the portfolio.
        """
        risk_contributions = {}
        
        for symbol, exposure in positions.items():
            volatility = volatilities.get(symbol, Decimal("0.20"))  # Default 20%
            
            # Calculate correlation factor
            if correlations:
                corr_factor = self._calculate_correlation_factor(
                    symbol, positions, correlations
                )
            else:
                corr_factor = Decimal("1.0")  # No correlation adjustment
            
            # Risk = exposure * volatility * correlation_factor
            risk = exposure * volatility * corr_factor
            risk_contributions[symbol] = risk
        
        return risk_contributions
    
    def _calculate_correlation_factor(
        self,
        symbol: str,
        positions: Dict[str, Decimal],
        correlations: Dict[Tuple[str, str], float]
    ) -> Decimal:
        """
        Calculate how correlated this position is with portfolio.
        
        Higher correlation with large positions = higher factor.
        """
        total_exposure = sum(positions.values())
        
        weighted_correlation = Decimal("0")
        
        for other_symbol, other_exposure in positions.items():
            if other_symbol == symbol:
                continue
            
            # Get correlation
            key1 = (symbol, other_symbol) if symbol < other_symbol else (other_symbol, symbol)
            corr = correlations.get(key1, 0.0)
            
            # Weight by exposure
            weight = other_exposure / total_exposure
            weighted_correlation += Decimal(str(abs(corr))) * weight
        
        # Factor ranges from 0.5 (uncorrelated) to 1.5 (highly correlated)
        factor = Decimal("0.5") + weighted_correlation
        
        return factor
    
    # ========================================================================
    # BUCKET BUILDING
    # ========================================================================
    
    def _build_symbol_buckets(
        self,
        positions: Dict[str, Decimal],
        risk_contributions: Dict[str, Decimal],
        total_exposure: Decimal,
        total_risk: Decimal,
        account_equity: Decimal
    ) -> List[RiskBucket]:
        """Build risk buckets by symbol."""
        buckets = []
        
        for symbol, exposure in positions.items():
            risk = risk_contributions[symbol]
            
            exposure_pct = (exposure / account_equity) * Decimal("100")
            risk_pct = (risk / total_risk) * Decimal("100") if total_risk > 0 else Decimal("0")
            
            level = self._get_concentration_level(risk_pct)
            
            bucket = RiskBucket(
                name=symbol,
                exposure=exposure,
                exposure_percent=exposure_pct,
                risk_contribution=risk,
                risk_percent=risk_pct,
                position_count=1,
                symbols={symbol},
                concentration_level=level
            )
            
            buckets.append(bucket)
        
        return sorted(buckets, key=lambda b: b.risk_percent, reverse=True)
    
    def _build_sector_buckets(
        self,
        positions: Dict[str, Decimal],
        risk_contributions: Dict[str, Decimal],
        total_exposure: Decimal,
        total_risk: Decimal,
        account_equity: Decimal
    ) -> List[RiskBucket]:
        """Build risk buckets by sector."""
        # Aggregate by sector
        sector_data: Dict[str, Dict] = {}
        
        for symbol, exposure in positions.items():
            sector = self.sector_map.get(symbol, "Unknown")
            risk = risk_contributions[symbol]
            
            if sector not in sector_data:
                sector_data[sector] = {
                    "exposure": Decimal("0"),
                    "risk": Decimal("0"),
                    "symbols": set(),
                    "count": 0
                }
            
            sector_data[sector]["exposure"] += exposure
            sector_data[sector]["risk"] += risk
            sector_data[sector]["symbols"].add(symbol)
            sector_data[sector]["count"] += 1
        
        # Build buckets
        buckets = []
        
        for sector, data in sector_data.items():
            exposure_pct = (data["exposure"] / account_equity) * Decimal("100")
            risk_pct = (data["risk"] / total_risk) * Decimal("100") if total_risk > 0 else Decimal("0")
            
            level = self._get_concentration_level(risk_pct)
            
            bucket = RiskBucket(
                name=sector,
                exposure=data["exposure"],
                exposure_percent=exposure_pct,
                risk_contribution=data["risk"],
                risk_percent=risk_pct,
                position_count=data["count"],
                symbols=data["symbols"],
                concentration_level=level
            )
            
            buckets.append(bucket)
        
        return sorted(buckets, key=lambda b: b.risk_percent, reverse=True)
    
    # ========================================================================
    # CONCENTRATION ANALYSIS
    # ========================================================================
    
    def _get_concentration_level(self, risk_percent: Decimal) -> ConcentrationLevel:
        """Determine concentration level from risk percentage."""
        if risk_percent < 20:
            return ConcentrationLevel.LOW
        elif risk_percent < 40:
            return ConcentrationLevel.MODERATE
        elif risk_percent < 60:
            return ConcentrationLevel.HIGH
        else:
            return ConcentrationLevel.EXTREME
    
    def _calculate_diversification_score(
        self,
        risk_contributions: Dict[str, Decimal],
        correlations: Optional[Dict[Tuple[str, str], float]]
    ) -> float:
        """
        Calculate diversification score (0-1).
        
        1.0 = perfectly diversified
        0.0 = perfectly concentrated
        """
        if len(risk_contributions) < 2:
            return 0.0  # Single position = no diversification
        
        total_risk = sum(risk_contributions.values())
        
        # Calculate HHI (Herfindahl-Hirschman Index)
        # Sum of squared risk shares
        hhi = sum(
            ((risk / total_risk) ** 2) for risk in risk_contributions.values()
        )
        
        # Convert to diversification score
        # HHI ranges from 1/n (perfectly diversified) to 1 (single position)
        n = len(risk_contributions)
        min_hhi = float(1 / n)
        max_hhi = 1.0
        
        # Normalize to 0-1 scale
        if max_hhi > min_hhi:
            score = 1.0 - ((float(hhi) - min_hhi) / (max_hhi - min_hhi))
        else:
            score = 1.0
        
        return max(0.0, min(1.0, score))
    
    def _generate_warnings(
        self,
        by_symbol: List[RiskBucket],
        by_sector: List[RiskBucket],
        diversification_score: float,
        max_concentration: Decimal
    ) -> List[str]:
        """Generate concentration warnings."""
        warnings = []
        
        # Check symbol concentration
        for bucket in by_symbol:
            if bucket.risk_percent > self.max_symbol_concentration:
                warnings.append(
                    f"Symbol {bucket.name}: {bucket.risk_percent:.1f}% risk "
                    f"(max: {self.max_symbol_concentration}%)"
                )
        
        # Check sector concentration
        for bucket in by_sector:
            if bucket.risk_percent > self.max_sector_concentration:
                warnings.append(
                    f"Sector {bucket.name}: {bucket.risk_percent:.1f}% risk "
                    f"(max: {self.max_sector_concentration}%)"
                )
        
        # Check diversification
        if diversification_score < self.min_diversification_score:
            warnings.append(
                f"Low diversification: {diversification_score:.2f} "
                f"(min: {self.min_diversification_score})"
            )
        
        # Check extreme concentration
        if max_concentration > 70:
            warnings.append(
                f"EXTREME concentration: {max_concentration:.1f}% in single bucket"
            )
        
        return warnings
    
    def _empty_heatmap(self) -> HeatmapData:
        """Return empty heatmap."""
        return HeatmapData(
            timestamp=datetime.now(timezone.utc),
            by_symbol=[],
            by_sector=[],
            top_risks=[],
            diversification_score=1.0,
            max_concentration=Decimal("0"),
            concentration_warnings=[]
        )
    
    # ========================================================================
    # SECTOR MANAGEMENT
    # ========================================================================
    
    def update_sector_map(self, symbol: str, sector: str):
        """Update sector mapping for a symbol."""
        self.sector_map[symbol] = sector
        self.logger.debug(f"Updated sector: {symbol} -> {sector}")
    
    def get_sector(self, symbol: str) -> str:
        """Get sector for a symbol."""
        return self.sector_map.get(symbol, "Unknown")
