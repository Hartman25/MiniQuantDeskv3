"""
Position reconciliation - syncs local state with broker state.

CRITICAL PROPERTIES:
1. Detects position drift (local vs broker)
2. Reconciles on startup
3. Logs all discrepancies
4. Never auto-trades to fix drift (manual intervention)
5. Circuit breaker on large drift

Based on defensive programming - trust broker as source of truth.
"""

from __future__ import annotations

# NOTE: This module is intentionally NOT wired into the active runtime.
# It is kept for reference only. Importing it should be harmless.
_DEPRECATED_WARNING_EMITTED = False

def _warn_deprecated() -> None:
    """Emit a deprecation warning the first time this module is *used*.

    We avoid warning at import-time because some environments treat warnings
    as errors (or spam logs) during test collection.
    """
    global _DEPRECATED_WARNING_EMITTED
    if _DEPRECATED_WARNING_EMITTED:
        return
    _DEPRECATED_WARNING_EMITTED = True
    try:
        import warnings
        warnings.warn(
            "core.execution.reconciliation is deprecated/unused; use core.state.reconciler instead.",
            DeprecationWarning,
            stacklevel=3,
        )
    except Exception:
        # Never fail because a warning couldn't be emitted.
        pass

from typing import List, Dict, Tuple
from decimal import Decimal
from datetime import datetime

from core.logging import get_logger, LogStream
from core.state import Position, PositionStore
from core.brokers.alpaca_connector import AlpacaBrokerConnector


# ============================================================================
# RECONCILIATION RESULT
# ============================================================================

class ReconciliationResult:
    """Position reconciliation result."""
    
    def __init__(self):
        self.matched: List[str] = []
        self.missing_local: List[Position] = []
        self.missing_broker: List[Position] = []
        self.quantity_mismatch: List[Tuple[Position, Position]] = []
        self.has_drift = False
    
    def to_dict(self) -> Dict:
        """Convert to dict."""
        return {
            "matched_count": len(self.matched),
            "missing_local_count": len(self.missing_local),
            "missing_broker_count": len(self.missing_broker),
            "quantity_mismatch_count": len(self.quantity_mismatch),
            "has_drift": self.has_drift
        }


# ============================================================================
# POSITION RECONCILIATION
# ============================================================================

class PositionReconciliation:
    """
    Position reconciliation engine.
    
    RULES:
    - Broker is source of truth
    - Never auto-trade to fix drift
    - Log all discrepancies
    - Circuit breaker on large drift
    
    USAGE:
        reconciler = PositionReconciliation(
            broker=broker_connector,
            position_store=position_store
        )
        
        # Reconcile on startup
        result = reconciler.reconcile()
        
        if result.has_drift:
            print("WARNING: Position drift detected!")
    """
    
    # Circuit breaker thresholds
    MAX_POSITION_DRIFT_PCT = 0.05  # 5% quantity drift tolerable
    MAX_MISSING_POSITIONS = 3      # Max positions that can be missing
    
    def __init__(
        self,
        broker: AlpacaBrokerConnector,
        position_store: PositionStore
    ):
        """Initialize reconciliation."""
        self.broker = broker
        self.position_store = position_store
        self.logger = get_logger(LogStream.POSITIONS)
        
        self.logger.info("PositionReconciliation initialized")
    
    def reconcile(self) -> ReconciliationResult:
    
        _warn_deprecated()
        """
        Reconcile positions between local store and broker.
        
        Returns:
            ReconciliationResult with discrepancies
        """
        result = ReconciliationResult()
        
        # Get positions from both sources
        broker_positions = self.broker.get_positions()
        local_positions = self.position_store.get_all()
        
        # Build lookup maps
        broker_map = {p.symbol: p for p in broker_positions}
        local_map = {p.symbol: p for p in local_positions}
        
        # Find matches and mismatches
        all_symbols = set(broker_map.keys()) | set(local_map.keys())
        
        for symbol in all_symbols:
            broker_pos = broker_map.get(symbol)
            local_pos = local_map.get(symbol)
            
            if broker_pos and local_pos:
                # Both exist - check quantity
                if broker_pos.quantity == local_pos.quantity:
                    result.matched.append(symbol)
                else:
                    result.quantity_mismatch.append((local_pos, broker_pos))
                    result.has_drift = True
                    
                    self.logger.warning(
                        f"Quantity mismatch: {symbol}",
                        extra={
                            "symbol": symbol,
                            "local_qty": str(local_pos.quantity),
                            "broker_qty": str(broker_pos.quantity),
                            "drift_pct": float(
                                abs(broker_pos.quantity - local_pos.quantity) /
                                broker_pos.quantity * 100
                            )
                        }
                    )
            
            elif broker_pos and not local_pos:
                # Missing in local
                result.missing_local.append(broker_pos)
                result.has_drift = True
                
                self.logger.warning(
                    f"Position missing in local store: {symbol}",
                    extra={
                        "symbol": symbol,
                        "broker_qty": str(broker_pos.quantity)
                    }
                )
            
            elif local_pos and not broker_pos:
                # Missing at broker
                result.missing_broker.append(local_pos)
                result.has_drift = True
                
                self.logger.error(
                    f"Position exists locally but not at broker: {symbol}",
                    extra={
                        "symbol": symbol,
                        "local_qty": str(local_pos.quantity)
                    }
                )
        
        # Check circuit breaker
        self._check_circuit_breaker(result)
        
        # Log summary
        self.logger.info(
            "Reconciliation complete",
            extra=result.to_dict()
        )
        
        return result
    
    def sync_from_broker(self) -> int:
        """
        Sync local positions from broker (trust broker).
        
        WARNING: This overwrites local positions!
        
        Returns:
            Number of positions synced
        """
        self.logger.warning("Syncing positions from broker (overwriting local)")
        
        broker_positions = self.broker.get_positions()
        
        # Clear local positions
        self.position_store.clear()
        
        # Insert all broker positions
        for pos in broker_positions:
            self.position_store.upsert(pos)
        
        self.logger.info(
            f"Synced {len(broker_positions)} positions from broker",
            extra={"count": len(broker_positions)}
        )
        
        return len(broker_positions)
    
    def _check_circuit_breaker(self, result: ReconciliationResult):
        """Check if drift exceeds circuit breaker thresholds."""
        # Check missing positions
        if len(result.missing_local) > self.MAX_MISSING_POSITIONS:
            raise ReconciliationError(
                f"Too many positions missing locally: {len(result.missing_local)} > {self.MAX_MISSING_POSITIONS}"
            )
        
        if len(result.missing_broker) > self.MAX_MISSING_POSITIONS:
            raise ReconciliationError(
                f"Too many positions missing at broker: {len(result.missing_broker)} > {self.MAX_MISSING_POSITIONS}"
            )
        
        # Check quantity drift
        for local_pos, broker_pos in result.quantity_mismatch:
            drift_pct = abs(
                float(broker_pos.quantity - local_pos.quantity) /
                float(broker_pos.quantity)
            )
            
            if drift_pct > self.MAX_POSITION_DRIFT_PCT:
                raise ReconciliationError(
                    f"Position drift too large for {local_pos.symbol}: "
                    f"{drift_pct*100:.1f}% > {self.MAX_POSITION_DRIFT_PCT*100:.1f}%"
                )


# ============================================================================
# EXCEPTIONS
# ============================================================================

class ReconciliationError(Exception):
    """Reconciliation error (circuit breaker tripped)."""
    pass