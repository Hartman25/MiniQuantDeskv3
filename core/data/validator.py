"""
Data Validator - Enforces MarketDataContract compliance and anti-lookahead.

CRITICAL RULES:
1. Validate ALL bars before strategy consumption
2. Check staleness (configurable threshold)
3. Detect gaps in time series
4. Validate OHLC relationships
5. Reject bad data immediately (fail-fast)
6. **NEW: Check bar completion to prevent lookahead bias**

Prevents look-ahead bias, stale data, and bad fills.
"""

from typing import List, Optional
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging

from core.data.contract import MarketDataContract, MarketDataContractError

logger = logging.getLogger(__name__)


# ============================================================================
# DATA VALIDATOR
# ============================================================================

class DataValidator:
    """
    Validates market data for correctness and freshness.
    
    VALIDATION STEPS:
    1. Schema compliance (via MarketDataContract.__post_init__)
    2. Staleness check
    3. **Bar completion check (ANTI-LOOKAHEAD)**
    4. Gap detection
    5. Duplicate detection
    6. Sort order validation
    
    Usage:
        validator = DataValidator(max_staleness_seconds=65)
        try:
            validator.validate_bars(bars, timeframe="1Min")
        except DataValidationError as e:
            logger.error(f"Bad data: {e}")
            # Reject data, do not pass to strategy
    """
    
    def __init__(
        self,
        max_staleness_seconds: int = 65,  # Reduced from 90s - tighter for 1Min bars
        allow_gaps: bool = True,
        max_gap_tolerance: float = 0.05,  # 5% missing bars acceptable
        require_complete_bars: bool = True  # NEW: Require bars to be complete
    ):
        """
        Initialize validator.
        
        Args:
            max_staleness_seconds: Maximum age for latest bar (65s for 1Min bars)
            allow_gaps: If False, any gaps raise error
            max_gap_tolerance: Fraction of missing bars allowed (0.0 to 1.0)
            require_complete_bars: If True, reject incomplete bars (anti-lookahead)
        """
        self.max_staleness_seconds = max_staleness_seconds
        self.allow_gaps = allow_gaps
        self.max_gap_tolerance = max_gap_tolerance
        self.require_complete_bars = require_complete_bars
        
        logger.info(
            f"DataValidator initialized "
            f"(max_staleness={max_staleness_seconds}s, "
            f"allow_gaps={allow_gaps}, "
            f"gap_tolerance={max_gap_tolerance:.1%}, "
            f"require_complete={require_complete_bars})"
        )
    
    def validate_bars(
        self,
        bars: List[MarketDataContract],
        timeframe: Optional[str] = None
    ) -> None:
        """
        Validate list of bars.
        
        CHECKS:
        - Non-empty list
        - All bars valid MarketDataContract
        - Latest bar not stale
        - **Latest bar is complete (if timeframe provided)**
        - No duplicates
        - Sorted by timestamp
        - No excessive gaps (if timeframe provided)
        
        Args:
            bars: List of bars to validate
            timeframe: Expected interval (for gap detection and completion check)
            
        Raises:
            DataValidationError: On any validation failure
        """
        if not bars:
            raise DataValidationError("Cannot validate empty bar list")
        
        # Check schema compliance (already done by __post_init__, but verify)
        for i, bar in enumerate(bars):
            if not isinstance(bar, MarketDataContract):
                raise DataValidationError(
                    f"Bar {i} is not MarketDataContract (got {type(bar)})"
                )
        
        # Check staleness of latest bar
        latest_bar = bars[-1]
        if latest_bar.is_stale(self.max_staleness_seconds):
            age = latest_bar.age_seconds()
            raise DataValidationError(
                f"Latest bar is stale: {latest_bar.symbol} "
                f"age={age:.1f}s > threshold={self.max_staleness_seconds}s "
                f"(timestamp={latest_bar.timestamp.isoformat()})"
            )
        
        # **CRITICAL: Check bar completion (anti-lookahead)**
        if self.require_complete_bars and timeframe:
            if not latest_bar.is_complete(timeframe):
                age = latest_bar.age_seconds()
                raise DataValidationError(
                    f"INCOMPLETE BAR REJECTED (anti-lookahead protection): "
                    f"{latest_bar.symbol} @ {latest_bar.timestamp.isoformat()} "
                    f"is not complete for {timeframe} timeframe (age={age:.1f}s). "
                    f"Using this bar would cause lookahead bias."
                )
        
        # Check for duplicates
        timestamps = [bar.timestamp for bar in bars]
        if len(timestamps) != len(set(timestamps)):
            raise DataValidationError(
                f"Duplicate timestamps detected in {bars[0].symbol}"
            )
        
        # Check sort order
        for i in range(1, len(bars)):
            if bars[i].timestamp <= bars[i-1].timestamp:
                raise DataValidationError(
                    f"Bars not sorted by timestamp: "
                    f"bar[{i-1}]={bars[i-1].timestamp} >= "
                    f"bar[{i}]={bars[i].timestamp}"
                )
        
        # Check gaps if timeframe provided
        if timeframe:
            self._check_gaps(bars, timeframe)
        
        logger.debug(
            f"Validated {len(bars)} bars for {bars[0].symbol} "
            f"({bars[0].timestamp} to {bars[-1].timestamp})"
        )
    
    def validate_single_bar(
        self,
        bar: MarketDataContract,
        timeframe: Optional[str] = None
    ) -> None:
        """
        Validate single bar for staleness and completion.
        
        Args:
            bar: Bar to validate
            timeframe: Expected interval (for completion check)
            
        Raises:
            DataValidationError: If bar is stale or incomplete
        """
        if not isinstance(bar, MarketDataContract):
            raise DataValidationError(
                f"Expected MarketDataContract, got {type(bar)}"
            )
        
        if bar.is_stale(self.max_staleness_seconds):
            age = bar.age_seconds()
            raise DataValidationError(
                f"Bar is stale: {bar.symbol} "
                f"age={age:.1f}s > threshold={self.max_staleness_seconds}s"
            )
        
        # **CRITICAL: Check bar completion (anti-lookahead)**
        if self.require_complete_bars and timeframe:
            if not bar.is_complete(timeframe):
                age = bar.age_seconds()
                raise DataValidationError(
                    f"INCOMPLETE BAR REJECTED: {bar.symbol} @ {bar.timestamp.isoformat()} "
                    f"is not complete for {timeframe} timeframe (age={age:.1f}s)"
                )
    
    def _check_gaps(self, bars: List[MarketDataContract], timeframe: str) -> None:
        """
        Check for gaps in time series.
        
        Args:
            bars: Sorted list of bars
            timeframe: Expected interval
            
        Raises:
            DataValidationError: If gaps exceed tolerance
        """
        # Calculate expected bars
        start = bars[0].timestamp
        end = bars[-1].timestamp
        expected_bars = self._calculate_expected_bars(start, end, timeframe)
        
        actual_bars = len(bars)
        missing_bars = expected_bars - actual_bars
        
        if missing_bars < 0:
            # More bars than expected (overlapping data?)
            logger.warning(
                f"More bars than expected: {actual_bars} > {expected_bars} "
                f"for {bars[0].symbol}"
            )
            return
        
        if missing_bars == 0:
            # Perfect data
            return
        
        # Calculate gap percentage
        gap_pct = missing_bars / expected_bars
        
        if not self.allow_gaps:
            raise DataValidationError(
                f"Gaps detected: {missing_bars} missing bars "
                f"({gap_pct:.1%} of expected {expected_bars})"
            )
        
        if gap_pct > self.max_gap_tolerance:
            raise DataValidationError(
                f"Excessive gaps: {missing_bars} missing bars "
                f"({gap_pct:.1%} > tolerance {self.max_gap_tolerance:.1%})"
            )
        
        logger.debug(
            f"Gaps within tolerance: {missing_bars} missing bars "
            f"({gap_pct:.1%} <= {self.max_gap_tolerance:.1%})"
        )
    
    @staticmethod
    def _calculate_expected_bars(
        start: datetime,
        end: datetime,
        timeframe: str
    ) -> int:
        """
        Calculate expected number of bars for time range.
        
        Simplified calculation (doesn't account for market hours).
        
        Args:
            start: Start time
            end: End time
            timeframe: Interval ("1Min", "5Min", "1Hour", "1Day")
            
        Returns:
            Expected bar count
        """
        duration = (end - start).total_seconds()
        
        # Map timeframe to seconds
        timeframe_seconds = {
            '1Min': 60,
            '5Min': 300,
            '15Min': 900,
            '30Min': 1800,
            '1Hour': 3600,
            '1Day': 86400
        }
        
        interval_seconds = timeframe_seconds.get(timeframe)
        if not interval_seconds:
            logger.warning(f"Unknown timeframe {timeframe}, skipping gap check")
            return 0
        
        expected = int(duration / interval_seconds) + 1
        return max(expected, 1)


# ============================================================================
# STALENESS VALIDATOR DECORATOR
# ============================================================================

def validate_staleness(max_age_seconds: int):
    """
    Decorator to validate bar staleness before function execution.
    
    Usage:
        @validate_staleness(max_age_seconds=65)
        def process_bar(bar: MarketDataContract):
            # Function will only execute if bar is fresh
            pass
    """
    def decorator(func):
        def wrapper(bar: MarketDataContract, *args, **kwargs):
            if bar.is_stale(max_age_seconds):
                raise DataValidationError(
                    f"Stale data: {bar.symbol} age={bar.age_seconds():.1f}s"
                )
            return func(bar, *args, **kwargs)
        return wrapper
    return decorator


# ============================================================================
# EXCEPTIONS
# ============================================================================

class DataValidationError(Exception):
    """Raised when data validation fails."""
    pass
