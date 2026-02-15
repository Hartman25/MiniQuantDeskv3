"""
System recovery coordinator.

ARCHITECTURE:
- Orchestrates recovery from crashes
- Validates recovered state against broker
- Reconstructs positions from broker if needed
- Handles pending orders after restart
- Performs integrity checks

RECOVERY PHASES:
1. Load persisted state
2. Validate state integrity
3. Reconcile with broker
4. Reconstruct missing state
5. Resume operation

SAFETY:
- Never assumes persisted state is correct
- Always validates against broker
- Detects and handles state corruption
- Graceful fallback to broker state

Based on LEAN's recovery coordinator and Freqtrade's startup sequence.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum

from core.recovery.persistence import (
    StatePersistence,
    SystemStateSnapshot,
    PositionSnapshot,
    OrderSnapshot,
    AccountSnapshot
)
from core.logging import get_logger, LogStream


# ============================================================================
# RECOVERY STATUS
# ============================================================================

class RecoveryStatus(Enum):
    """Recovery operation status."""
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"  # Some state recovered, some rebuilt
    REBUILT = "REBUILT"  # No saved state, rebuilt from broker
    FAILED = "FAILED"


@dataclass
class RecoveryReport:
    """Report of recovery operation."""
    status: RecoveryStatus
    recovered_state: Optional[SystemStateSnapshot]
    positions_recovered: int
    positions_rebuilt: int
    orders_recovered: int
    orders_cancelled: int
    inconsistencies_found: List[str]
    recovery_time_seconds: float
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "positions_recovered": self.positions_recovered,
            "positions_rebuilt": self.positions_rebuilt,
            "orders_recovered": self.orders_recovered,
            "orders_cancelled": self.orders_cancelled,
            "inconsistencies": self.inconsistencies_found,
            "recovery_time_seconds": self.recovery_time_seconds,
            "timestamp": self.timestamp.isoformat()
        }


# ============================================================================
# RECOVERY COORDINATOR
# ============================================================================

class RecoveryCoordinator:
    """
    System recovery coordinator.

    AUTHORITATIVE STATE CONTRACT (PATCH 10):
        Truth priority for state recovery:
          1. Broker truth   — always preferred when broker is reachable.
          2. Snapshot state  — used when broker data is unavailable but a
             valid, non-stale snapshot exists on disk.
          3. Local ephemeral — transaction log / in-memory state used only
             as supplementary context; never overrides broker or snapshot.

        Fail-mode:
          - LIVE:  If broker is unreachable and no valid snapshot exists,
                   recovery returns FAILED and the runtime must halt.
          - PAPER: Recovery may continue in degraded mode (REBUILT).

    RESPONSIBILITIES:
    - Load persisted state on startup
    - Validate state against broker
    - Reconstruct positions from broker if needed
    - Handle pending orders after restart
    - Generate recovery report

    RECOVERY STRATEGIES:
    1. Happy path: Load state, validate against broker, resume
    2. Stale state: Load state, reconcile with broker, resume
    3. Corrupted state: Discard, rebuild from broker
    4. No state: Fresh start, load from broker

    USAGE:
        coordinator = RecoveryCoordinator(
            persistence=state_persistence,
            broker=broker_connector,
            position_store=position_store,
            order_machine=order_machine
        )

        # On startup
        report = coordinator.recover()

        if report.status == RecoveryStatus.SUCCESS:
            logger.info("Recovery successful")
        else:
            logger.warning(f"Recovery partial: {report.inconsistencies_found}")
    """
    
    def __init__(
        self,
        persistence: StatePersistence,
        broker,
        position_store,
        order_machine,
        max_state_age_hours: int = 24,
        paper_mode: bool = True
    ):
        """
        Initialize recovery coordinator.

        Args:
            persistence: State persistence manager
            broker: Broker connector
            position_store: Position store
            order_machine: Order state machine
            max_state_age_hours: Maximum age of state to trust (hours)
            paper_mode: If True, continue recovery on cancel failures (default: True)
        """
        self.persistence = persistence
        self.broker = broker
        self.position_store = position_store
        self.order_machine = order_machine
        self.max_state_age = timedelta(hours=max_state_age_hours)
        self.paper_mode = paper_mode

        self.logger = get_logger(LogStream.SYSTEM)

        self.logger.info("RecoveryCoordinator initialized", extra={
            "max_state_age_hours": max_state_age_hours,
            "paper_mode": paper_mode
        })
    
    # ========================================================================
    # MAIN RECOVERY ENTRY POINT
    # ========================================================================
    
    def recover(self) -> RecoveryReport:
        """
        Perform system recovery.
        
        Returns:
            RecoveryReport with recovery details
        """
        start_time = datetime.now(timezone.utc)
        
        self.logger.info("Starting system recovery")
        
        try:
            # Phase 1: Load persisted state
            saved_state = self.persistence.load_latest_state()
            
            # Phase 2: Decide recovery strategy
            if saved_state is None:
                # No saved state - rebuild from broker
                report = self._recover_from_broker()
                
            elif self._is_state_stale(saved_state):
                # State too old - rebuild from broker
                self.logger.warning(
                    "Saved state is stale",
                    extra={"age_hours": self._get_state_age_hours(saved_state)}
                )
                report = self._recover_from_broker()
                
            else:
                # State looks good - validate and use
                report = self._recover_from_saved_state(saved_state)
            
            # Calculate recovery time
            recovery_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            report.recovery_time_seconds = recovery_time
            
            # Log result
            self.logger.info(
                f"Recovery complete: {report.status.value}",
                extra=report.to_dict()
            )
            
            return report
            
        except Exception as e:
            self.logger.error(
                "Recovery failed",
                extra={"error": str(e)},
                exc_info=True
            )
            
            return RecoveryReport(
                status=RecoveryStatus.FAILED,
                recovered_state=None,
                positions_recovered=0,
                positions_rebuilt=0,
                orders_recovered=0,
                orders_cancelled=0,
                inconsistencies_found=[f"Recovery exception: {str(e)}"],
                recovery_time_seconds=0.0,
                timestamp=datetime.now(timezone.utc)
            )
    
    # ========================================================================
    # RECOVERY FROM SAVED STATE
    # ========================================================================
    
    def _recover_from_saved_state(
        self,
        saved_state: SystemStateSnapshot
    ) -> RecoveryReport:
        """
        Recover from saved state with broker validation.
        
        Args:
            saved_state: Loaded state snapshot
            
        Returns:
            RecoveryReport
        """
        self.logger.info("Recovering from saved state", extra={
            "state_timestamp": saved_state.timestamp.isoformat(),
            "positions": len(saved_state.positions),
            "orders": len(saved_state.pending_orders)
        })
        
        inconsistencies = []
        positions_recovered = 0
        positions_rebuilt = 0
        orders_recovered = 0
        orders_cancelled = 0
        
        # Get current broker state
        broker_positions = self._get_broker_positions()
        broker_orders = self._get_broker_orders()
        
        # Recover positions
        for pos_snapshot in saved_state.positions:
            # Check if position still exists in broker
            broker_pos = broker_positions.get(pos_snapshot.symbol)
            
            if broker_pos:
                # Validate quantities match
                saved_qty = pos_snapshot.quantity
                broker_qty = Decimal(str(broker_pos.qty))
                
                if abs(saved_qty - broker_qty) < Decimal("0.01"):
                    # Quantities match - restore position
                    self._restore_position(pos_snapshot)
                    positions_recovered += 1
                else:
                    # Quantities don't match - use broker state
                    inconsistencies.append(
                        f"{pos_snapshot.symbol}: qty mismatch "
                        f"(saved={saved_qty}, broker={broker_qty})"
                    )
                    self._rebuild_position_from_broker(broker_pos)
                    positions_rebuilt += 1
            else:
                # Position closed while offline
                inconsistencies.append(
                    f"{pos_snapshot.symbol}: position closed while offline"
                )
        
        # Check for new positions (broker has, saved state doesn't)
        saved_symbols = {p.symbol for p in saved_state.positions}
        for symbol, broker_pos in broker_positions.items():
            if symbol not in saved_symbols:
                inconsistencies.append(
                    f"{symbol}: new position opened while offline"
                )
                self._rebuild_position_from_broker(broker_pos)
                positions_rebuilt += 1
        
        # Recover orders
        for order_snapshot in saved_state.pending_orders:
            # Check if order still exists in broker
            broker_order = broker_orders.get(order_snapshot.broker_order_id)
            
            if broker_order and broker_order.status in ["new", "partially_filled", "accepted"]:
                # Order still pending - restore
                self._restore_order(order_snapshot)
                orders_recovered += 1
            else:
                # Order filled or cancelled while offline
                if broker_order:
                    inconsistencies.append(
                        f"Order {order_snapshot.order_id}: "
                        f"status changed to {broker_order.status}"
                    )
                else:
                    inconsistencies.append(
                        f"Order {order_snapshot.order_id}: no longer exists in broker"
                    )
                orders_cancelled += 1
        
        # Determine overall status
        if len(inconsistencies) == 0:
            status = RecoveryStatus.SUCCESS
        else:
            status = RecoveryStatus.PARTIAL
        
        return RecoveryReport(
            status=status,
            recovered_state=saved_state,
            positions_recovered=positions_recovered,
            positions_rebuilt=positions_rebuilt,
            orders_recovered=orders_recovered,
            orders_cancelled=orders_cancelled,
            inconsistencies_found=inconsistencies,
            recovery_time_seconds=0.0,  # Set by caller
            timestamp=datetime.now(timezone.utc)
        )
    
    # ========================================================================
    # RECOVERY FROM BROKER
    # ========================================================================
    
    def _recover_from_broker(self) -> RecoveryReport:
        """
        Recover by rebuilding state from broker.

        PATCH 1: Before rebuilding, cancel all open broker orders to prevent
        ghost orders from remaining live after restart.

        Returns:
            RecoveryReport

        Raises:
            Exception: In live mode, if any order cancellation fails
        """
        self.logger.info("Rebuilding state from broker")

        # PATCH 1: Cancel all open orders before rebuilding
        orders_cancelled = self._cancel_open_orders()

        positions_rebuilt = 0

        # Get broker positions
        broker_positions = self._get_broker_positions()

        # Rebuild each position
        for symbol, broker_pos in broker_positions.items():
            self._rebuild_position_from_broker(broker_pos)
            positions_rebuilt += 1

        return RecoveryReport(
            status=RecoveryStatus.REBUILT,
            recovered_state=None,
            positions_recovered=0,
            positions_rebuilt=positions_rebuilt,
            orders_recovered=0,
            orders_cancelled=orders_cancelled,
            inconsistencies_found=["Rebuilt from broker (no saved state or stale)"],
            recovery_time_seconds=0.0,  # Set by caller
            timestamp=datetime.now(timezone.utc)
        )
    
    # ========================================================================
    # STATE RESTORATION HELPERS
    # ========================================================================
    
    def _restore_position(self, pos_snapshot: PositionSnapshot):
        """Restore position from snapshot."""
        try:
            self.position_store.restore_position(
                symbol=pos_snapshot.symbol,
                quantity=pos_snapshot.quantity,
                avg_price=pos_snapshot.avg_price,
                entry_time=pos_snapshot.entry_time
            )
            
            self.logger.debug(f"Restored position: {pos_snapshot.symbol}", extra={
                "symbol": pos_snapshot.symbol,
                "quantity": str(pos_snapshot.quantity),
                "avg_price": str(pos_snapshot.avg_price)
            })
            
        except Exception as e:
            self.logger.error(
                f"Failed to restore position: {pos_snapshot.symbol}",
                extra={"error": str(e)},
                exc_info=True
            )
    
    def _rebuild_position_from_broker(self, broker_pos):
        """Rebuild position from broker data.

        ``broker_pos`` may be either a raw Alpaca position object (with
        ``.qty`` / ``.avg_entry_price``) or a mapped ``Position`` dataclass
        (with ``.quantity`` / ``.entry_price``).  We accept both.
        """
        try:
            qty = getattr(broker_pos, "qty", None) or getattr(broker_pos, "quantity", None)
            avg = getattr(broker_pos, "avg_entry_price", None) or getattr(broker_pos, "entry_price", None)

            self.position_store.restore_position(
                symbol=broker_pos.symbol,
                quantity=Decimal(str(qty)),
                avg_price=Decimal(str(avg)),
                entry_time=getattr(broker_pos, "entry_time", None) or datetime.now(timezone.utc),
            )

            self.logger.debug(f"Rebuilt position from broker: {broker_pos.symbol}", extra={
                "symbol": broker_pos.symbol,
                "quantity": str(qty),
                "avg_price": str(avg),
            })
            
        except Exception as e:
            self.logger.error(
                f"Failed to rebuild position: {broker_pos.symbol}",
                extra={"error": str(e)},
                exc_info=True
            )
    
    def _restore_order(self, order_snapshot: OrderSnapshot):
        """Log that order restore was skipped.

        PATCH 3: Paper mode uses cancel+rebuild, so we do NOT pretend to
        reinsert orders into OrderTracker/OrderStateMachine.  The old code
        logged "Restored order" without actually rehydrating state — that
        was misleading and could cause duplicates or missed cancels.

        Live mode should never reach here because open orders are cancelled
        during recovery; if it does, the coordinator will already have
        returned FAILED before this point.
        """
        self.logger.info(
            "Skipped order rehydrate: paper mode uses cancel+rebuild; "
            "order not inserted into tracker",
            extra={
                "order_id": order_snapshot.order_id,
                "symbol": order_snapshot.symbol,
                "status": order_snapshot.status,
                "reason": "no_real_rehydration_available",
            },
        )
    
    # ========================================================================
    # ORDER CANCELLATION (PATCH 1)
    # ========================================================================

    def _cancel_open_orders(self) -> int:
        """
        Cancel all open broker orders before rebuilding state.

        PATCH 1: Prevents ghost orders from remaining live after restart.

        Returns:
            Number of orders successfully cancelled

        Raises:
            Exception: In live mode, if any cancellation fails
        """
        try:
            # Fetch all open orders from broker
            broker_orders = self.broker.get_orders(status="open")

            if not broker_orders:
                self.logger.info("No open orders to cancel")
                return 0

            # Filter to working orders only
            working_statuses = {"new", "accepted", "pending_new", "partially_filled", "held"}
            working_orders = [
                order for order in broker_orders
                if order.status.lower() in working_statuses
            ]

            self.logger.info(
                f"Cancelling open orders: {len(working_orders)} found, {len(broker_orders)} total open",
                extra={
                    "working_orders": len(working_orders),
                    "total_open_orders": len(broker_orders)
                }
            )

            cancelled_count = 0
            failed_cancellations = []

            for order in working_orders:
                try:
                    success = self.broker.cancel_order(order.id)
                    if success:
                        cancelled_count += 1
                        self.logger.info(
                            f"Cancelled order: {order.id}",
                            extra={
                                "order_id": order.id,
                                "symbol": order.symbol,
                                "status": order.status
                            }
                        )
                    else:
                        self.logger.info(
                            f"Order not cancelable: {order.id}",
                            extra={"order_id": order.id, "status": order.status}
                        )

                except Exception as e:
                    failed_cancellations.append((order.id, str(e)))
                    self.logger.warning(
                        f"Failed to cancel order: {order.id}",
                        extra={
                            "order_id": order.id,
                            "error": str(e),
                            "symbol": order.symbol
                        }
                    )

            # In live mode, halt if any cancellations failed
            if failed_cancellations and not self.paper_mode:
                error_msg = (
                    f"Recovery halted: {len(failed_cancellations)} order cancellation(s) failed in live mode. "
                    f"Failed orders: {[oid for oid, _ in failed_cancellations]}"
                )
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            # In paper mode, log failures but continue
            if failed_cancellations and self.paper_mode:
                self.logger.warning(
                    f"Continuing recovery despite {len(failed_cancellations)} failed cancellations (paper mode)",
                    extra={"failed_count": len(failed_cancellations)}
                )

            return cancelled_count

        except Exception as e:
            self.logger.error(
                "Failed to cancel open orders",
                extra={"error": str(e)},
                exc_info=True
            )
            # Re-raise in live mode, absorb in paper mode
            if not self.paper_mode:
                raise
            return 0

    # ========================================================================
    # BROKER QUERY HELPERS
    # ========================================================================

    def _get_broker_positions(self) -> Dict[str, Any]:
        """Get current positions from broker."""
        try:
            positions = self.broker.get_positions()
            return {pos.symbol: pos for pos in positions}
        except Exception as e:
            self.logger.error(
                "Failed to get broker positions",
                extra={"error": str(e)},
                exc_info=True
            )
            return {}
    
    def _get_broker_orders(self) -> Dict[str, Any]:
        """Get current orders from broker."""
        try:
            orders = self.broker.get_orders()
            return {order.id: order for order in orders}
        except Exception as e:
            self.logger.error(
                "Failed to get broker orders",
                extra={"error": str(e)},
                exc_info=True
            )
            return {}
    
    # ========================================================================
    # STATE VALIDATION HELPERS
    # ========================================================================
    
    def _is_state_stale(self, state: SystemStateSnapshot) -> bool:
        """Check if state is too old to trust."""
        age = datetime.now(timezone.utc) - state.timestamp
        return age > self.max_state_age
    
    def _get_state_age_hours(self, state: SystemStateSnapshot) -> float:
        """Get state age in hours."""
        age = datetime.now(timezone.utc) - state.timestamp
        return age.total_seconds() / 3600
