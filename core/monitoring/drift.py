"""
Real-time position drift detection system.

ARCHITECTURE:
- Continuous monitoring of position discrepancies
- Real-time comparison: local state vs broker state
- Automatic reconciliation on minor drift
- Alert + halt on major drift
- Drift history tracking for analysis

DRIFT TYPES:
- Position quantity mismatch
- Position price mismatch
- Unknown positions (broker has, we don't)
- Ghost positions (we have, broker doesn't)

SAFETY:
- Critical component for live trading
- Prevents position drift from becoming losses
- Early detection of reconciliation failures

Based on LEAN's PortfolioValidator and Freqtrade's balance checks.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Set
from enum import Enum

from core.logging import get_logger, LogStream


# ============================================================================
# DRIFT DEFINITIONS
# ============================================================================

class DriftType(Enum):
    """Type of position drift."""
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    UNKNOWN_POSITION = "UNKNOWN_POSITION"  # Broker has, we don't
    GHOST_POSITION = "GHOST_POSITION"      # We have, broker doesn't
    NO_DRIFT = "NO_DRIFT"


class DriftSeverity(Enum):
    """Severity of detected drift."""
    MINOR = "MINOR"        # Within tolerance, auto-reconcile
    MODERATE = "MODERATE"  # Exceeds tolerance, alert
    CRITICAL = "CRITICAL"  # Large drift, halt trading


@dataclass
class PositionState:
    """Position state (local or broker)."""
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    side: str  # LONG or SHORT
    timestamp: datetime
    source: str  # "LOCAL" or "BROKER"
    
    def value(self) -> Decimal:
        """Calculate position value."""
        return abs(self.quantity * self.avg_price)


@dataclass
class PositionDrift:
    """Detected position drift."""
    symbol: str
    drift_type: DriftType
    severity: DriftSeverity
    local_state: Optional[PositionState]
    broker_state: Optional[PositionState]
    detected_at: datetime
    
    # Calculated metrics
    quantity_delta: Optional[Decimal] = None
    value_delta: Optional[Decimal] = None
    price_delta: Optional[Decimal] = None
    
    def __post_init__(self):
        """Calculate drift metrics."""
        if self.local_state and self.broker_state:
            self.quantity_delta = self.broker_state.quantity - self.local_state.quantity
            self.value_delta = self.broker_state.value() - self.local_state.value()
            self.price_delta = self.broker_state.avg_price - self.local_state.avg_price
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "drift_type": self.drift_type.value,
            "severity": self.severity.value,
            "local_state": {
                "quantity": str(self.local_state.quantity),
                "avg_price": str(self.local_state.avg_price),
                "value": str(self.local_state.value())
            } if self.local_state else None,
            "broker_state": {
                "quantity": str(self.broker_state.quantity),
                "avg_price": str(self.broker_state.avg_price),
                "value": str(self.broker_state.value())
            } if self.broker_state else None,
            "quantity_delta": str(self.quantity_delta) if self.quantity_delta else None,
            "value_delta": str(self.value_delta) if self.value_delta else None,
            "price_delta": str(self.price_delta) if self.price_delta else None,
            "detected_at": self.detected_at.isoformat()
        }


# ============================================================================
# DRIFT DETECTOR
# ============================================================================

class DriftDetector:
    """
    Real-time position drift detection.
    
    RESPONSIBILITIES:
    - Compare local positions vs broker positions
    - Detect discrepancies in real-time
    - Classify drift severity
    - Trigger alerts on threshold violations
    - Auto-reconcile minor drift
    - Halt trading on critical drift
    
    DESIGN:
    - Inspired by LEAN's PortfolioValidator
    - Checks every 60 seconds (configurable)
    - Three severity levels (MINOR/MODERATE/CRITICAL)
    - Tolerance-based classification
    - Historical drift tracking
    
    THRESHOLDS:
    - MINOR: ±1 share or ±$10 value
    - MODERATE: ±5 shares or ±$100 value
    - CRITICAL: ±10 shares or ±$500 value
    
    USAGE:
        detector = DriftDetector(
            position_store=position_store,
            broker=broker,
            check_interval_seconds=60
        )
        
        # Run check
        drifts = detector.check_drift()
        
        if any(d.severity == DriftSeverity.CRITICAL for d in drifts):
            # HALT TRADING
            trading_engine.halt("Critical position drift detected")
            alert_manager.send_critical("Position drift: halting")
        
        # Auto-reconcile minor drift
        for drift in drifts:
            if drift.severity == DriftSeverity.MINOR:
                detector.auto_reconcile(drift)
    """
    
    def __init__(
        self,
        position_store,
        broker,
        check_interval_seconds: int = 60,
        minor_quantity_threshold: Decimal = Decimal("1"),
        minor_value_threshold: Decimal = Decimal("10"),
        moderate_quantity_threshold: Decimal = Decimal("5"),
        moderate_value_threshold: Decimal = Decimal("100"),
        critical_quantity_threshold: Decimal = Decimal("10"),
        critical_value_threshold: Decimal = Decimal("500")
    ):
        """
        Initialize drift detector.
        
        Args:
            position_store: Local position store
            broker: Broker connector
            check_interval_seconds: How often to check for drift
            minor_quantity_threshold: Minor drift quantity threshold
            minor_value_threshold: Minor drift value threshold
            moderate_quantity_threshold: Moderate drift quantity threshold
            moderate_value_threshold: Moderate drift value threshold
            critical_quantity_threshold: Critical drift quantity threshold
            critical_value_threshold: Critical drift value threshold
        """
        self.position_store = position_store
        self.broker = broker
        self.check_interval = check_interval_seconds
        
        # Thresholds
        self.minor_qty_threshold = minor_quantity_threshold
        self.minor_val_threshold = minor_value_threshold
        self.moderate_qty_threshold = moderate_quantity_threshold
        self.moderate_val_threshold = moderate_value_threshold
        self.critical_qty_threshold = critical_quantity_threshold
        self.critical_val_threshold = critical_value_threshold
        
        self.logger = get_logger(LogStream.SYSTEM)
        
        # Drift history
        self._drift_history: List[PositionDrift] = []
        self._max_history = 1000
        
        # Last check timestamp
        self._last_check: Optional[datetime] = None
        
        # Auto-reconciliation tracking
        self._auto_reconciled_count = 0
        self._manual_reconciliation_required: Set[str] = set()
        
        self.logger.info("DriftDetector initialized", extra={
            "check_interval": check_interval_seconds,
            "minor_thresholds": {
                "quantity": str(minor_quantity_threshold),
                "value": str(minor_value_threshold)
            },
            "moderate_thresholds": {
                "quantity": str(moderate_quantity_threshold),
                "value": str(moderate_value_threshold)
            },
            "critical_thresholds": {
                "quantity": str(critical_quantity_threshold),
                "value": str(critical_value_threshold)
            }
        })
    
    # ========================================================================
    # DRIFT DETECTION
    # ========================================================================
    
    def check_drift(self) -> List[PositionDrift]:
        """
        Check for position drift.
        
        Returns:
            List of detected drifts (empty if no drift)
        """
        self._last_check = datetime.now(timezone.utc)
        
        try:
            # Get local positions
            local_positions = self._get_local_positions()
            
            # Get broker positions
            broker_positions = self._get_broker_positions()
            
            # Compare positions
            drifts = self._compare_positions(local_positions, broker_positions)
            
            # Store in history
            self._drift_history.extend(drifts)
            
            # Trim history
            if len(self._drift_history) > self._max_history:
                self._drift_history = self._drift_history[-self._max_history:]
            
            # Log results
            if drifts:
                for drift in drifts:
                    log_level = "error" if drift.severity == DriftSeverity.CRITICAL else \
                               "warning" if drift.severity == DriftSeverity.MODERATE else "info"
                    
                    getattr(self.logger, log_level)(
                        f"Position drift detected: {drift.symbol}",
                        extra={
                            "symbol": drift.symbol,
                            "drift_type": drift.drift_type.value,
                            "severity": drift.severity.value,
                            "quantity_delta": str(drift.quantity_delta) if drift.quantity_delta else None,
                            "value_delta": str(drift.value_delta) if drift.value_delta else None
                        }
                    )
            else:
                self.logger.debug("No position drift detected")
            
            return drifts
            
        except Exception as e:
            self.logger.error(
                "Drift check failed",
                extra={"error": str(e)},
                exc_info=True
            )
            return []
    
    def _get_local_positions(self) -> Dict[str, PositionState]:
        """Get local positions from position store."""
        positions = {}
        
        for symbol in self.position_store.get_symbols():
            pos = self.position_store.get_position(symbol)
            if pos and pos.quantity != 0:
                positions[symbol] = PositionState(
                    symbol=symbol,
                    quantity=pos.quantity,
                    avg_price=pos.avg_price,
                    side="LONG" if pos.quantity > 0 else "SHORT",
                    timestamp=datetime.now(timezone.utc),
                    source="LOCAL"
                )
        
        return positions
    
    def _get_broker_positions(self) -> Dict[str, PositionState]:
        """Get broker positions from broker API."""
        positions = {}
        
        try:
            broker_positions = self.broker.get_positions()
            
            for pos in broker_positions:
                positions[pos.symbol] = PositionState(
                    symbol=pos.symbol,
                    quantity=Decimal(str(pos.qty)),
                    avg_price=Decimal(str(pos.avg_entry_price)),
                    side=pos.side,
                    timestamp=datetime.now(timezone.utc),
                    source="BROKER"
                )
        except Exception as e:
            self.logger.error(
                "Failed to get broker positions",
                extra={"error": str(e)},
                exc_info=True
            )
        
        return positions
    
    def _compare_positions(
        self,
        local: Dict[str, PositionState],
        broker: Dict[str, PositionState]
    ) -> List[PositionDrift]:
        """Compare local vs broker positions and detect drift."""
        drifts = []
        
        # Get all symbols
        all_symbols = set(local.keys()) | set(broker.keys())
        
        for symbol in all_symbols:
            local_pos = local.get(symbol)
            broker_pos = broker.get(symbol)
            
            # Case 1: Position exists in both
            if local_pos and broker_pos:
                drift = self._check_position_match(local_pos, broker_pos)
                if drift:
                    drifts.append(drift)
            
            # Case 2: Position only in broker (unknown position)
            elif broker_pos and not local_pos:
                severity = self._classify_severity(
                    qty_delta=broker_pos.quantity,
                    val_delta=broker_pos.value()
                )
                
                drifts.append(PositionDrift(
                    symbol=symbol,
                    drift_type=DriftType.UNKNOWN_POSITION,
                    severity=severity,
                    local_state=None,
                    broker_state=broker_pos,
                    detected_at=datetime.now(timezone.utc)
                ))
            
            # Case 3: Position only in local (ghost position)
            elif local_pos and not broker_pos:
                severity = self._classify_severity(
                    qty_delta=local_pos.quantity,
                    val_delta=local_pos.value()
                )
                
                drifts.append(PositionDrift(
                    symbol=symbol,
                    drift_type=DriftType.GHOST_POSITION,
                    severity=severity,
                    local_state=local_pos,
                    broker_state=None,
                    detected_at=datetime.now(timezone.utc)
                ))
        
        return drifts
    
    def _check_position_match(
        self,
        local: PositionState,
        broker: PositionState
    ) -> Optional[PositionDrift]:
        """Check if local and broker positions match."""
        
        # Check quantity mismatch
        qty_delta = abs(broker.quantity - local.quantity)
        val_delta = abs(broker.value() - local.value())
        
        if qty_delta > 0:
            severity = self._classify_severity(qty_delta, val_delta)
            
            return PositionDrift(
                symbol=local.symbol,
                drift_type=DriftType.QUANTITY_MISMATCH,
                severity=severity,
                local_state=local,
                broker_state=broker,
                detected_at=datetime.now(timezone.utc)
            )
        
        # Check price mismatch (if quantity matches)
        price_delta = abs(broker.avg_price - local.avg_price)
        if price_delta > Decimal("0.01"):  # More than 1 cent difference
            # Price mismatch is usually minor
            return PositionDrift(
                symbol=local.symbol,
                drift_type=DriftType.PRICE_MISMATCH,
                severity=DriftSeverity.MINOR,
                local_state=local,
                broker_state=broker,
                detected_at=datetime.now(timezone.utc)
            )
        
        return None  # No drift
    
    def _classify_severity(
        self,
        qty_delta: Decimal,
        val_delta: Decimal
    ) -> DriftSeverity:
        """Classify drift severity based on thresholds."""
        
        # Critical drift
        if qty_delta >= self.critical_qty_threshold or val_delta >= self.critical_val_threshold:
            return DriftSeverity.CRITICAL
        
        # Moderate drift
        if qty_delta >= self.moderate_qty_threshold or val_delta >= self.moderate_val_threshold:
            return DriftSeverity.MODERATE
        
        # Minor drift
        if qty_delta >= self.minor_qty_threshold or val_delta >= self.minor_val_threshold:
            return DriftSeverity.MINOR
        
        return DriftSeverity.MINOR  # Default
    
    # ========================================================================
    # AUTO-RECONCILIATION
    # ========================================================================
    
    def auto_reconcile(self, drift: PositionDrift) -> bool:
        """
        Attempt automatic reconciliation of minor drift.
        
        Args:
            drift: Detected drift to reconcile
            
        Returns:
            True if reconciled successfully, False otherwise
        """
        if drift.severity != DriftSeverity.MINOR:
            self.logger.warning(
                f"Cannot auto-reconcile {drift.severity.value} drift",
                extra={"symbol": drift.symbol}
            )
            return False
        
        try:
            if drift.drift_type == DriftType.QUANTITY_MISMATCH:
                # Update local position to match broker
                if drift.broker_state:
                    self.position_store.sync_position(
                        symbol=drift.symbol,
                        quantity=drift.broker_state.quantity,
                        avg_price=drift.broker_state.avg_price
                    )
                    
                    self._auto_reconciled_count += 1
                    
                    self.logger.info(
                        f"Auto-reconciled position: {drift.symbol}",
                        extra={
                            "symbol": drift.symbol,
                            "new_quantity": str(drift.broker_state.quantity),
                            "new_avg_price": str(drift.broker_state.avg_price)
                        }
                    )
                    return True
            
            elif drift.drift_type == DriftType.PRICE_MISMATCH:
                # Update local price to match broker
                if drift.broker_state:
                    self.position_store.update_avg_price(
                        symbol=drift.symbol,
                        new_avg_price=drift.broker_state.avg_price
                    )
                    
                    self._auto_reconciled_count += 1
                    
                    self.logger.info(
                        f"Auto-reconciled price: {drift.symbol}",
                        extra={
                            "symbol": drift.symbol,
                            "new_avg_price": str(drift.broker_state.avg_price)
                        }
                    )
                    return True
            
            else:
                # Unknown/Ghost positions require manual intervention
                self._manual_reconciliation_required.add(drift.symbol)
                return False
                
        except Exception as e:
            self.logger.error(
                f"Auto-reconciliation failed: {drift.symbol}",
                extra={"symbol": drift.symbol, "error": str(e)},
                exc_info=True
            )
            return False
    
    # ========================================================================
    # QUERY METHODS
    # ========================================================================
    
    def get_drift_history(
        self,
        lookback_minutes: int = 60,
        symbol: Optional[str] = None
    ) -> List[PositionDrift]:
        """Get drift history for time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        
        history = [
            d for d in self._drift_history
            if d.detected_at >= cutoff
        ]
        
        if symbol:
            history = [d for d in history if d.symbol == symbol]
        
        return history
    
    def get_symbols_requiring_manual_reconciliation(self) -> Set[str]:
        """Get symbols that require manual reconciliation."""
        return self._manual_reconciliation_required.copy()
    
    def clear_manual_reconciliation_flag(self, symbol: str):
        """Clear manual reconciliation flag after resolving."""
        self._manual_reconciliation_required.discard(symbol)
    
    def get_auto_reconciliation_stats(self) -> Dict:
        """Get auto-reconciliation statistics."""
        return {
            "total_auto_reconciled": self._auto_reconciled_count,
            "symbols_requiring_manual": len(self._manual_reconciliation_required),
            "last_check": self._last_check.isoformat() if self._last_check else None
        }
