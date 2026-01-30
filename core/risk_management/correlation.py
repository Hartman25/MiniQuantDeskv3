"""
Correlation matrix tracking and exposure management.

ARCHITECTURE:
- Rolling correlation calculation
- Real-time correlation matrix
- Correlated exposure limits
- Cluster risk detection
- Diversification scoring

DESIGN PRINCIPLE:
Limit correlated risk, not just individual position risk.

EXAMPLE:
- Holding: SPY, QQQ, AAPL, MSFT, NVDA
- All highly correlated (0.8+)
- Effective positions: ~2 (not 5!)
- Risk is concentrated, not diversified

This prevents getting wiped out by sector moves.

Based on Bridgewater's risk parity and Markowitz portfolio theory.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
from collections import deque
import math

from core.logging import get_logger, LogStream


# ============================================================================
# CORRELATION DATA
# ============================================================================

@dataclass
class CorrelationPair:
    """Correlation between two symbols."""
    symbol1: str
    symbol2: str
    correlation: float  # -1.0 to 1.0
    sample_size: int
    last_updated: datetime
    
    def is_high_correlation(self, threshold: float = 0.7) -> bool:
        """Check if correlation is high."""
        return abs(self.correlation) >= threshold
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol1": self.symbol1,
            "symbol2": self.symbol2,
            "correlation": round(self.correlation, 3),
            "sample_size": self.sample_size,
            "last_updated": self.last_updated.isoformat()
        }


@dataclass
class CorrelationCluster:
    """Cluster of highly correlated symbols."""
    symbols: Set[str]
    avg_correlation: float
    total_exposure: Decimal
    risk_concentration: float  # 0-1 (1=all in one cluster)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbols": sorted(list(self.symbols)),
            "avg_correlation": round(self.avg_correlation, 3),
            "total_exposure": str(self.total_exposure),
            "risk_concentration": round(self.risk_concentration, 3)
        }


# ============================================================================
# CORRELATION MATRIX TRACKER
# ============================================================================

class CorrelationMatrix:
    """
    Real-time correlation matrix tracking.
    
    RESPONSIBILITIES:
    - Track price returns for correlation
    - Calculate rolling correlation
    - Identify correlation clusters
    - Limit correlated exposure
    - Calculate portfolio diversification
    
    CORRELATION CALCULATION:
    - Uses daily returns (close-to-close)
    - Rolling window (default 30 days)
    - Pearson correlation coefficient
    - Updates daily
    
    USAGE:
        tracker = CorrelationMatrix(
            lookback_days=30,
            min_correlation_samples=20,
            high_correlation_threshold=0.7
        )
        
        # Update daily with returns
        tracker.update_returns("SPY", Decimal("0.01"))  # +1% return
        tracker.update_returns("QQQ", Decimal("0.012"))  # +1.2% return
        
        # Check correlation
        corr = tracker.get_correlation("SPY", "QQQ")
        print(f"SPY-QQQ correlation: {corr}")
        
        # Find clusters
        clusters = tracker.find_clusters()
        for cluster in clusters:
            print(f"Cluster: {cluster.symbols}, exposure: {cluster.total_exposure}")
    """
    
    def __init__(
        self,
        lookback_days: int = 30,
        min_correlation_samples: int = 20,
        high_correlation_threshold: float = 0.7,
        max_correlated_exposure_percent: Decimal = Decimal("25.0")
    ):
        """
        Initialize correlation tracker.
        
        Args:
            lookback_days: Days of history for correlation
            min_correlation_samples: Minimum samples to calculate correlation
            high_correlation_threshold: Threshold for "high correlation"
            max_correlated_exposure_percent: Max % exposure to correlated group
        """
        self.lookback_days = lookback_days
        self.min_correlation_samples = min_correlation_samples
        self.high_correlation_threshold = high_correlation_threshold
        self.max_correlated_exposure = max_correlated_exposure_percent
        
        self.logger = get_logger(LogStream.RISK)
        
        # Returns history: {symbol: deque of returns}
        self._returns: Dict[str, deque] = {}
        
        # Cached correlations: {(symbol1, symbol2): CorrelationPair}
        self._correlations: Dict[Tuple[str, str], CorrelationPair] = {}
        
        # Last update time
        self._last_update: Optional[datetime] = None
        
        self.logger.info("CorrelationMatrix initialized", extra={
            "lookback_days": lookback_days,
            "high_correlation_threshold": high_correlation_threshold
        })
    
    # ========================================================================
    # RETURNS TRACKING
    # ========================================================================
    
    def update_returns(
        self,
        symbol: str,
        return_pct: Decimal,
        timestamp: Optional[datetime] = None
    ):
        """
        Update daily returns for a symbol.
        
        Args:
            symbol: Stock symbol
            return_pct: Daily return as percentage (e.g., 1.5 for +1.5%)
            timestamp: Return timestamp (defaults to now)
        """
        if symbol not in self._returns:
            self._returns[symbol] = deque(maxlen=self.lookback_days)
        
        self._returns[symbol].append(float(return_pct))
        self._last_update = timestamp or datetime.now(timezone.utc)
        
        # Invalidate cached correlations for this symbol
        self._invalidate_correlations_for_symbol(symbol)
    
    def _invalidate_correlations_for_symbol(self, symbol: str):
        """Invalidate cached correlations involving a symbol."""
        to_remove = [
            key for key in self._correlations.keys()
            if symbol in key
        ]
        for key in to_remove:
            del self._correlations[key]
    
    # ========================================================================
    # CORRELATION CALCULATION
    # ========================================================================
    
    def get_correlation(
        self,
        symbol1: str,
        symbol2: str,
        force_recalculate: bool = False
    ) -> Optional[float]:
        """
        Get correlation between two symbols.
        
        Args:
            symbol1: First symbol
            symbol2: Second symbol
            force_recalculate: Force recalculation even if cached
            
        Returns:
            Correlation (-1.0 to 1.0) or None if insufficient data
        """
        # Normalize order
        if symbol1 > symbol2:
            symbol1, symbol2 = symbol2, symbol1
        
        key = (symbol1, symbol2)
        
        # Check cache
        if not force_recalculate and key in self._correlations:
            return self._correlations[key].correlation
        
        # Calculate correlation
        corr_pair = self._calculate_correlation(symbol1, symbol2)
        
        if corr_pair:
            self._correlations[key] = corr_pair
            return corr_pair.correlation
        
        return None
    
    def _calculate_correlation(
        self,
        symbol1: str,
        symbol2: str
    ) -> Optional[CorrelationPair]:
        """Calculate correlation between two symbols."""
        # Get returns
        returns1 = self._returns.get(symbol1)
        returns2 = self._returns.get(symbol2)
        
        if not returns1 or not returns2:
            return None
        
        # Need matching sample size
        sample_size = min(len(returns1), len(returns2))
        
        if sample_size < self.min_correlation_samples:
            return None
        
        # Get aligned returns
        r1 = list(returns1)[-sample_size:]
        r2 = list(returns2)[-sample_size:]
        
        # Calculate correlation (Pearson)
        corr = self._pearson_correlation(r1, r2)
        
        if corr is None:
            return None
        
        return CorrelationPair(
            symbol1=symbol1,
            symbol2=symbol2,
            correlation=corr,
            sample_size=sample_size,
            last_updated=datetime.now(timezone.utc)
        )
    
    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        
        if n == 0:
            return None
        
        # Calculate means
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        
        # Calculate covariance and standard deviations
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
        
        if std_x == 0 or std_y == 0:
            return None
        
        corr = cov / (std_x * std_y)
        
        # Clamp to [-1, 1]
        return max(-1.0, min(1.0, corr))
    
    # ========================================================================
    # CORRELATION MATRIX
    # ========================================================================
    
    def get_correlation_matrix(
        self,
        symbols: Optional[List[str]] = None
    ) -> Dict[Tuple[str, str], float]:
        """
        Get full correlation matrix.
        
        Args:
            symbols: List of symbols (defaults to all tracked)
            
        Returns:
            Dictionary of (symbol1, symbol2) -> correlation
        """
        if symbols is None:
            symbols = list(self._returns.keys())
        
        matrix = {}
        
        for i, sym1 in enumerate(symbols):
            for sym2 in symbols[i+1:]:
                corr = self.get_correlation(sym1, sym2)
                if corr is not None:
                    key = (sym1, sym2) if sym1 < sym2 else (sym2, sym1)
                    matrix[key] = corr
        
        return matrix
    
    def get_highly_correlated_pairs(
        self,
        threshold: Optional[float] = None
    ) -> List[CorrelationPair]:
        """
        Get all pairs with correlation above threshold.
        
        Args:
            threshold: Correlation threshold (defaults to class threshold)
            
        Returns:
            List of highly correlated pairs
        """
        threshold = threshold or self.high_correlation_threshold
        
        pairs = []
        for corr_pair in self._correlations.values():
            if corr_pair.is_high_correlation(threshold):
                pairs.append(corr_pair)
        
        return sorted(pairs, key=lambda p: abs(p.correlation), reverse=True)
    
    # ========================================================================
    # CLUSTER DETECTION
    # ========================================================================
    
    def find_clusters(
        self,
        symbols: Optional[List[str]] = None,
        positions: Optional[Dict[str, Decimal]] = None
    ) -> List[CorrelationCluster]:
        """
        Find clusters of highly correlated symbols.
        
        Args:
            symbols: Symbols to analyze (defaults to all tracked)
            positions: Position sizes for exposure calculation
            
        Returns:
            List of correlation clusters
        """
        if symbols is None:
            symbols = list(self._returns.keys())
        
        # Build adjacency list (symbols connected if correlated)
        adjacency: Dict[str, Set[str]] = {sym: set() for sym in symbols}
        
        for sym1 in symbols:
            for sym2 in symbols:
                if sym1 == sym2:
                    continue
                
                corr = self.get_correlation(sym1, sym2)
                if corr and abs(corr) >= self.high_correlation_threshold:
                    adjacency[sym1].add(sym2)
        
        # Find connected components (clusters)
        visited = set()
        clusters = []
        
        for symbol in symbols:
            if symbol in visited:
                continue
            
            # BFS to find cluster
            cluster_symbols = set()
            queue = [symbol]
            
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                
                visited.add(current)
                cluster_symbols.add(current)
                
                # Add connected symbols
                for neighbor in adjacency[current]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            
            # Calculate cluster metrics
            if len(cluster_symbols) >= 2:
                cluster = self._create_cluster(cluster_symbols, positions)
                clusters.append(cluster)
        
        return sorted(clusters, key=lambda c: c.total_exposure, reverse=True)
    
    def _create_cluster(
        self,
        symbols: Set[str],
        positions: Optional[Dict[str, Decimal]]
    ) -> CorrelationCluster:
        """Create cluster with metrics."""
        # Calculate average correlation within cluster
        correlations = []
        for sym1 in symbols:
            for sym2 in symbols:
                if sym1 < sym2:
                    corr = self.get_correlation(sym1, sym2)
                    if corr is not None:
                        correlations.append(abs(corr))
        
        avg_corr = sum(correlations) / len(correlations) if correlations else 0.0
        
        # Calculate total exposure
        total_exposure = Decimal("0")
        if positions:
            for symbol in symbols:
                total_exposure += positions.get(symbol, Decimal("0"))
        
        # Calculate risk concentration (simplified)
        risk_concentration = len(symbols) / max(1, len(positions) if positions else 1)
        
        return CorrelationCluster(
            symbols=symbols,
            avg_correlation=avg_corr,
            total_exposure=total_exposure,
            risk_concentration=min(1.0, risk_concentration)
        )
    
    # ========================================================================
    # EXPOSURE LIMITS
    # ========================================================================
    
    def check_correlated_exposure(
        self,
        symbol: str,
        new_exposure: Decimal,
        current_positions: Dict[str, Decimal],
        account_equity: Decimal
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if adding exposure violates correlation limits.
        
        Args:
            symbol: Symbol to check
            new_exposure: New exposure to add
            current_positions: Current position exposures
            account_equity: Account equity
            
        Returns:
            (allowed, reason) - True if allowed, False with reason if not
        """
        # Find which cluster this symbol belongs to
        symbols = list(current_positions.keys()) + [symbol]
        clusters = self.find_clusters(symbols, current_positions)
        
        # Find cluster containing the new symbol
        for cluster in clusters:
            if symbol in cluster.symbols:
                # Calculate new cluster exposure
                new_cluster_exposure = cluster.total_exposure + new_exposure
                max_allowed = (account_equity * self.max_correlated_exposure) / Decimal("100")
                
                if new_cluster_exposure > max_allowed:
                    return False, (
                        f"Correlated exposure limit: {cluster.symbols} "
                        f"would be {new_cluster_exposure} "
                        f"(max {max_allowed})"
                    )
        
        return True, None
    
    # ========================================================================
    # DIVERSIFICATION SCORING
    # ========================================================================
    
    def calculate_diversification_score(
        self,
        positions: Dict[str, Decimal]
    ) -> float:
        """
        Calculate portfolio diversification score (0-1).
        
        1.0 = Perfectly diversified (no correlations)
        0.0 = Perfectly concentrated (all correlated)
        
        Args:
            positions: Position exposures
            
        Returns:
            Diversification score (0-1)
        """
        if len(positions) < 2:
            return 1.0  # Single position is trivially "diversified"
        
        symbols = list(positions.keys())
        
        # Calculate average absolute correlation
        correlations = []
        for i, sym1 in enumerate(symbols):
            for sym2 in symbols[i+1:]:
                corr = self.get_correlation(sym1, sym2)
                if corr is not None:
                    correlations.append(abs(corr))
        
        if not correlations:
            return 1.0  # No correlations means diversified
        
        avg_corr = sum(correlations) / len(correlations)
        
        # Convert to score (1 - correlation)
        return 1.0 - avg_corr
