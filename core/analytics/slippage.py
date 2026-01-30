"""
Slippage analysis and tracking.

ARCHITECTURE:
- Track expected vs actual fill prices
- Aggregate slippage by symbol
- Aggregate by time of day
- Aggregate by order size
- Pattern detection

DESIGN PRINCIPLE:
Slippage is a hidden cost - measure it.

SLIPPAGE CALCULATION:
- BUY: (Actual Price - Expected Price) / Expected Price
- SELL: (Expected Price - Actual Price) / Expected Price

EXAMPLE:
Expected: $100.00
Actual: $100.10
Slippage: 0.10% (10 basis points)

Based on institutional execution quality analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, time, timedelta
from decimal import Decimal
from typing import List, Dict, Optional
from collections import defaultdict
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# SLIPPAGE RECORD
# ============================================================================

@dataclass
class SlippageRecord:
    """Individual slippage record."""
    symbol: str
    timestamp: datetime
    side: str  # "BUY" or "SELL"
    expected_price: Decimal
    actual_price: Decimal
    quantity: Decimal
    slippage_dollars: Decimal
    slippage_bps: int  # Basis points (1/100 of 1%)
    time_to_fill_ms: int  # Milliseconds from submission to fill
    order_type: str  # "MARKET", "LIMIT", etc.
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "side": self.side,
            "expected_price": str(self.expected_price),
            "actual_price": str(self.actual_price),
            "quantity": str(self.quantity),
            "slippage_dollars": str(self.slippage_dollars),
            "slippage_bps": self.slippage_bps,
            "time_to_fill_ms": self.time_to_fill_ms,
            "order_type": self.order_type
        }


# ============================================================================
# SLIPPAGE STATISTICS
# ============================================================================

@dataclass
class SlippageStatistics:
    """Aggregated slippage statistics."""
    category: str  # "symbol", "time_of_day", "order_size", "overall"
    subcategory: str  # e.g., "AAPL", "09:30-10:00", "small", "all"
    
    sample_count: int
    
    avg_slippage_bps: float
    median_slippage_bps: float
    max_slippage_bps: int
    min_slippage_bps: int
    
    avg_slippage_dollars: Decimal
    total_slippage_dollars: Decimal
    
    avg_time_to_fill_ms: float
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "sample_count": self.sample_count,
            "avg_slippage_bps": round(self.avg_slippage_bps, 1),
            "median_slippage_bps": round(self.median_slippage_bps, 1),
            "max_slippage_bps": self.max_slippage_bps,
            "min_slippage_bps": self.min_slippage_bps,
            "avg_slippage_dollars": str(self.avg_slippage_dollars),
            "total_slippage_dollars": str(self.total_slippage_dollars),
            "avg_time_to_fill_ms": round(self.avg_time_to_fill_ms, 0)
        }


# ============================================================================
# SLIPPAGE ANALYZER
# ============================================================================

class SlippageAnalyzer:
    """
    Track and analyze execution slippage.
    
    RESPONSIBILITIES:
    - Record expected vs actual prices
    - Calculate slippage in bps and dollars
    - Aggregate by multiple dimensions
    - Identify patterns
    - Alert on excessive slippage
    
    ANALYSIS DIMENSIONS:
    - By Symbol: Which symbols have worst slippage
    - By Time: When is slippage highest (e.g., market open)
    - By Order Size: How size affects slippage
    - By Order Type: Market vs Limit orders
    
    USAGE:
        analyzer = SlippageAnalyzer(
            alert_threshold_bps=50  # Alert if >50 bps
        )
        
        # Record execution
        analyzer.record_execution(
            symbol="AAPL",
            side="BUY",
            expected_price=Decimal("185.00"),
            actual_price=Decimal("185.10"),
            quantity=Decimal("100"),
            time_to_fill_ms=150,
            order_type="MARKET"
        )
        
        # Get statistics
        stats = analyzer.get_statistics_by_symbol("AAPL")
        print(f"Avg slippage: {stats.avg_slippage_bps} bps")
        
        # Check patterns
        worst = analyzer.get_worst_slippage_times()
    """
    
    def __init__(
        self,
        alert_threshold_bps: int = 50,
        max_records: int = 10000
    ):
        """
        Initialize slippage analyzer.
        
        Args:
            alert_threshold_bps: Threshold for alerting (basis points)
            max_records: Maximum records to keep (rolling window)
        """
        self.alert_threshold_bps = alert_threshold_bps
        self.max_records = max_records
        
        self.logger = get_logger(LogStream.ANALYTICS)
        
        # Storage
        self.records: List[SlippageRecord] = []
        
        self.logger.info("SlippageAnalyzer initialized", extra={
            "alert_threshold_bps": alert_threshold_bps
        })
    
    # ========================================================================
    # RECORDING
    # ========================================================================
    
    def record_execution(
        self,
        symbol: str,
        side: str,
        expected_price: Decimal,
        actual_price: Decimal,
        quantity: Decimal,
        time_to_fill_ms: int = 0,
        order_type: str = "MARKET",
        timestamp: Optional[datetime] = None
    ):
        """
        Record an execution.
        
        Args:
            symbol: Stock symbol
            side: "BUY" or "SELL"
            expected_price: Expected fill price
            actual_price: Actual fill price
            quantity: Order quantity
            time_to_fill_ms: Time from submission to fill (ms)
            order_type: Order type
            timestamp: Execution timestamp (defaults to now)
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        
        # Calculate slippage
        if side == "BUY":
            # Positive slippage = paid more than expected
            slippage_dollars = (actual_price - expected_price) * quantity
            slippage_pct = (actual_price - expected_price) / expected_price if expected_price > 0 else Decimal("0")
        else:  # SELL
            # Positive slippage = received less than expected
            slippage_dollars = (expected_price - actual_price) * quantity
            slippage_pct = (expected_price - actual_price) / expected_price if expected_price > 0 else Decimal("0")
        
        slippage_bps = int(float(slippage_pct) * 10000)  # Convert to basis points
        
        record = SlippageRecord(
            symbol=symbol,
            timestamp=timestamp,
            side=side,
            expected_price=expected_price,
            actual_price=actual_price,
            quantity=quantity,
            slippage_dollars=slippage_dollars,
            slippage_bps=slippage_bps,
            time_to_fill_ms=time_to_fill_ms,
            order_type=order_type
        )
        
        self.records.append(record)
        
        # Trim if needed
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]
        
        # Alert if excessive
        if abs(slippage_bps) > self.alert_threshold_bps:
            self.logger.warning(
                f"High slippage detected: {symbol}",
                extra=record.to_dict()
            )
        
        self.logger.debug(f"Slippage recorded: {symbol}", extra={
            "slippage_bps": slippage_bps,
            "slippage_dollars": str(slippage_dollars)
        })
    
    # ========================================================================
    # STATISTICS BY SYMBOL
    # ========================================================================
    
    def get_statistics_by_symbol(self, symbol: Optional[str] = None) -> Dict[str, SlippageStatistics]:
        """
        Get slippage statistics by symbol.
        
        Args:
            symbol: Specific symbol (or None for all)
            
        Returns:
            Dictionary of symbol -> statistics
        """
        if symbol:
            records = [r for r in self.records if r.symbol == symbol]
            return {symbol: self._calculate_statistics(records, "symbol", symbol)}
        else:
            # Group by symbol
            by_symbol = defaultdict(list)
            for record in self.records:
                by_symbol[record.symbol].append(record)
            
            return {
                sym: self._calculate_statistics(recs, "symbol", sym)
                for sym, recs in by_symbol.items()
            }
    
    # ========================================================================
    # STATISTICS BY TIME OF DAY
    # ========================================================================
    
    def get_statistics_by_time_of_day(self) -> Dict[str, SlippageStatistics]:
        """
        Get slippage statistics by time of day.
        
        Returns:
            Dictionary of time_bucket -> statistics
        """
        # Define time buckets
        buckets = {
            "Market Open (09:30-10:00)": (time(9, 30), time(10, 0)),
            "Morning (10:00-11:30)": (time(10, 0), time(11, 30)),
            "Midday (11:30-14:00)": (time(11, 30), time(14, 0)),
            "Afternoon (14:00-15:30)": (time(14, 0), time(15, 30)),
            "Market Close (15:30-16:00)": (time(15, 30), time(16, 0))
        }
        
        # Group records by bucket
        by_bucket = defaultdict(list)
        
        for record in self.records:
            record_time = record.timestamp.time()
            
            for bucket_name, (start, end) in buckets.items():
                if start <= record_time < end:
                    by_bucket[bucket_name].append(record)
                    break
        
        return {
            bucket: self._calculate_statistics(recs, "time_of_day", bucket)
            for bucket, recs in by_bucket.items()
            if recs  # Only include if we have data
        }
    
    # ========================================================================
    # STATISTICS BY ORDER SIZE
    # ========================================================================
    
    def get_statistics_by_order_size(self) -> Dict[str, SlippageStatistics]:
        """
        Get slippage statistics by order size.
        
        Returns:
            Dictionary of size_bucket -> statistics
        """
        # Group by size
        by_size = {
            "Small (1-50)": [],
            "Medium (51-200)": [],
            "Large (201-500)": [],
            "Very Large (500+)": []
        }
        
        for record in self.records:
            qty = float(record.quantity)
            
            if qty <= 50:
                by_size["Small (1-50)"].append(record)
            elif qty <= 200:
                by_size["Medium (51-200)"].append(record)
            elif qty <= 500:
                by_size["Large (201-500)"].append(record)
            else:
                by_size["Very Large (500+)"].append(record)
        
        return {
            bucket: self._calculate_statistics(recs, "order_size", bucket)
            for bucket, recs in by_size.items()
            if recs
        }
    
    # ========================================================================
    # STATISTICS CALCULATION
    # ========================================================================
    
    def _calculate_statistics(
        self,
        records: List[SlippageRecord],
        category: str,
        subcategory: str
    ) -> SlippageStatistics:
        """Calculate statistics for a set of records."""
        if not records:
            return SlippageStatistics(
                category=category,
                subcategory=subcategory,
                sample_count=0,
                avg_slippage_bps=0.0,
                median_slippage_bps=0.0,
                max_slippage_bps=0,
                min_slippage_bps=0,
                avg_slippage_dollars=Decimal("0"),
                total_slippage_dollars=Decimal("0"),
                avg_time_to_fill_ms=0.0
            )
        
        slippage_bps_list = sorted([r.slippage_bps for r in records])
        
        avg_bps = sum(slippage_bps_list) / len(slippage_bps_list)
        median_bps = slippage_bps_list[len(slippage_bps_list) // 2]
        
        total_dollars = sum(r.slippage_dollars for r in records)
        avg_dollars = total_dollars / len(records)
        
        avg_time = sum(r.time_to_fill_ms for r in records) / len(records)
        
        return SlippageStatistics(
            category=category,
            subcategory=subcategory,
            sample_count=len(records),
            avg_slippage_bps=avg_bps,
            median_slippage_bps=median_bps,
            max_slippage_bps=max(slippage_bps_list),
            min_slippage_bps=min(slippage_bps_list),
            avg_slippage_dollars=avg_dollars,
            total_slippage_dollars=total_dollars,
            avg_time_to_fill_ms=avg_time
        )
    
    # ========================================================================
    # PATTERN ANALYSIS
    # ========================================================================
    
    def get_worst_slippage_symbols(self, count: int = 5) -> List[tuple]:
        """
        Get symbols with worst average slippage.
        
        Returns:
            List of (symbol, avg_slippage_bps)
        """
        stats = self.get_statistics_by_symbol()
        
        sorted_stats = sorted(
            stats.items(),
            key=lambda x: x[1].avg_slippage_bps,
            reverse=True
        )
        
        return [(sym, stats.avg_slippage_bps) for sym, stats in sorted_stats[:count]]
    
    def get_worst_slippage_times(self) -> List[tuple]:
        """
        Get times of day with worst slippage.
        
        Returns:
            List of (time_bucket, avg_slippage_bps)
        """
        stats = self.get_statistics_by_time_of_day()
        
        sorted_stats = sorted(
            stats.items(),
            key=lambda x: x[1].avg_slippage_bps,
            reverse=True
        )
        
        return [(bucket, stats.avg_slippage_bps) for bucket, stats in sorted_stats]
    
    # ========================================================================
    # REPORTING
    # ========================================================================
    
    def get_overall_statistics(self) -> SlippageStatistics:
        """Get overall slippage statistics."""
        return self._calculate_statistics(self.records, "overall", "all")
    
    def get_comprehensive_report(self) -> Dict:
        """Get comprehensive slippage report."""
        return {
            "overall": self.get_overall_statistics().to_dict(),
            "by_symbol": {
                sym: stats.to_dict()
                for sym, stats in self.get_statistics_by_symbol().items()
            },
            "by_time_of_day": {
                bucket: stats.to_dict()
                for bucket, stats in self.get_statistics_by_time_of_day().items()
            },
            "by_order_size": {
                bucket: stats.to_dict()
                for bucket, stats in self.get_statistics_by_order_size().items()
            },
            "worst_symbols": [
                {"symbol": sym, "avg_slippage_bps": bps}
                for sym, bps in self.get_worst_slippage_symbols()
            ],
            "worst_times": [
                {"time_bucket": bucket, "avg_slippage_bps": bps}
                for bucket, bps in self.get_worst_slippage_times()
            ]
        }
