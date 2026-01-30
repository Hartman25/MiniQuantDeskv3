"""
Market Data Contract - Unified schema for all data providers.

CRITICAL RULES:
1. All fields mandatory except volume (for forex/crypto)
2. Decimal precision for prices (NOT float)
3. Timezone-aware datetime (UTC only)
4. Immutable dataclass (frozen=True)
5. Validation on creation (__post_init__)
6. ALL providers must conform to this schema
7. ANTI-LOOKAHEAD: Check is_complete() before using bar.close

This is the single source of truth for market data structure.
Based on LEAN's BaseData hierarchy with enhanced validation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# MARKET DATA CONTRACT
# ============================================================================

@dataclass(frozen=True)
class MarketDataContract:
    """
    Unified market data schema - ALL providers MUST conform.
    
    INVARIANTS:
    - timestamp is timezone-aware UTC
    - Prices are Decimal for precision
    - Prices must be positive
    - Volume cannot be negative
    - Symbol is uppercase
    
    ANTI-LOOKAHEAD PROTECTION:
    - Use is_complete(timeframe) before consuming bar.close
    - Incomplete bars contain partial data and will cause lookahead bias
    
    Example:
        bar = MarketDataContract(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc),
            open=Decimal("580.50"),
            high=Decimal("581.25"),
            low=Decimal("579.80"),
            close=Decimal("580.90"),
            volume=1000000,
            provider="alpaca"
        )
        
        # CRITICAL: Check completion before using close price
        if bar.is_complete(timeframe="1Min"):
            # Safe to use bar.close
            strategy.on_bar(bar)
    """
    
    symbol: str                    # Ticker symbol (uppercase)
    timestamp: datetime            # Bar start time, UTC, timezone-aware
    open: Decimal                  # Open price
    high: Decimal                  # High price
    low: Decimal                   # Low price
    close: Decimal                 # Close price (ONLY FINAL if bar is complete!)
    volume: Optional[int]          # Volume (shares), None for forex/crypto if unavailable
    provider: str = "unknown"      # Data provider name (e.g., "alpaca", "polygon")
    
    def __post_init__(self):
        """Validate contract on creation - fail fast."""
        
        # Validate timezone
        if self.timestamp.tzinfo is None:
            raise MarketDataContractError(
                f"timestamp must be timezone-aware (got naive datetime)"
            )
        
        # Validate prices are positive
        if self.open <= 0:
            raise MarketDataContractError(
                f"open price must be positive (got {self.open})"
            )
        if self.high <= 0:
            raise MarketDataContractError(
                f"high price must be positive (got {self.high})"
            )
        if self.low <= 0:
            raise MarketDataContractError(
                f"low price must be positive (got {self.low})"
            )
        if self.close <= 0:
            raise MarketDataContractError(
                f"close price must be positive (got {self.close})"
            )
        
        # Validate high/low relationships
        if self.high < self.low:
            raise MarketDataContractError(
                f"high ({self.high}) cannot be less than low ({self.low})"
            )
        if self.high < self.open:
            raise MarketDataContractError(
                f"high ({self.high}) cannot be less than open ({self.open})"
            )
        if self.high < self.close:
            raise MarketDataContractError(
                f"high ({self.high}) cannot be less than close ({self.close})"
            )
        if self.low > self.open:
            raise MarketDataContractError(
                f"low ({self.low}) cannot be greater than open ({self.open})"
            )
        if self.low > self.close:
            raise MarketDataContractError(
                f"low ({self.low}) cannot be greater than close ({self.close})"
            )
        
        # Validate volume
        if self.volume is not None and self.volume < 0:
            raise MarketDataContractError(
                f"volume cannot be negative (got {self.volume})"
            )
        
        # Normalize symbol to uppercase
        object.__setattr__(self, 'symbol', self.symbol.upper())
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            'symbol': self.symbol,
            'timestamp': self.timestamp.isoformat(),
            'open': str(self.open),
            'high': str(self.high),
            'low': str(self.low),
            'close': str(self.close),
            'volume': self.volume,
            'provider': self.provider
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'MarketDataContract':
        """Create from dictionary."""
        return cls(
            symbol=data['symbol'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            open=Decimal(data['open']),
            high=Decimal(data['high']),
            low=Decimal(data['low']),
            close=Decimal(data['close']),
            volume=data.get('volume'),
            provider=data.get('provider', 'unknown')
        )
    
    def age_seconds(self, reference_time: Optional[datetime] = None) -> float:
        """
        Calculate age of this bar in seconds.
        
        Args:
            reference_time: Time to measure against (default: now UTC)
            
        Returns:
            Age in seconds (positive = bar is in past)
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        
        return (reference_time - self.timestamp).total_seconds()
    
    def is_stale(self, max_age_seconds: int) -> bool:
        """
        Check if bar is stale (too old).
        
        Args:
            max_age_seconds: Maximum acceptable age
            
        Returns:
            True if bar is older than threshold
        """
        return self.age_seconds() > max_age_seconds
    
    def is_complete(
        self,
        timeframe: str,
        reference_time: Optional[datetime] = None,
        grace_period_seconds: int = 5
    ) -> bool:
        """
        Check if bar has fully closed (anti-lookahead protection).
        
        CRITICAL FOR PREVENTING LOOKAHEAD BIAS:
        A bar is only "complete" if its time period has fully elapsed.
        
        Example:
            bar.timestamp = 2024-01-20 09:30:00 (start of 1-minute bar)
            timeframe = "1Min"
            current_time = 2024-01-20 09:30:45
            
            → Bar closes at 09:31:00
            → Current time 09:30:45 < 09:31:00
            → Bar is NOT complete
            → bar.close is FUTURE data (should not be used)
        
        Args:
            timeframe: Bar interval ("1Min", "5Min", "15Min", "1Hour", "1Day")
            reference_time: Current time (default: now UTC)
            grace_period_seconds: Extra seconds to wait after close (default: 5)
            
        Returns:
            True if bar has fully closed and grace period elapsed
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        
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
        if interval_seconds is None:
            # Unknown timeframe - log warning and assume complete
            logger.warning(
                f"Unknown timeframe '{timeframe}' for completion check, assuming complete"
            )
            return True
        
        # Calculate when this bar closes
        bar_close_time = self.timestamp + timedelta(seconds=interval_seconds)
        
        # Calculate when it's safe to use (after grace period)
        safe_time = bar_close_time + timedelta(seconds=grace_period_seconds)
        
        # Bar is complete if we're past the safe time
        is_complete = reference_time >= safe_time
        
        if not is_complete:
            seconds_until_complete = (safe_time - reference_time).total_seconds()
            logger.debug(
                f"Bar {self.symbol} @ {self.timestamp} is INCOMPLETE "
                f"({seconds_until_complete:.1f}s remaining until {timeframe} bar closes)"
            )
        
        return is_complete


# ============================================================================
# EXCEPTIONS
# ============================================================================

class MarketDataContractError(Exception):
    """Raised when MarketDataContract validation fails."""
    pass
