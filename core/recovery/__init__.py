"""
Recovery system package.

AUTOMATED RECOVERY (+4 Safety Points)

COMPONENTS:
- StatePersistence: Periodic state snapshots with atomic writes
- RecoveryCoordinator: Crash recovery orchestration
- ResilientDataProvider: Graceful degradation for data feeds

DESIGN STANDARDS:
- LEAN-inspired state management
- Freqtrade-quality resilience
- Write-ahead logging pattern
- Atomic file operations

USAGE:
    from core.recovery import (
        StatePersistence,
        SystemStateSnapshot,
        RecoveryCoordinator,
        RecoveryStatus,
        ResilientDataProvider,
        ProviderStatus
    )
    
    # Initialize persistence
    persistence = StatePersistence(
        state_dir=Path("state"),
        backup_count=5
    )
    
    # Save state periodically
    snapshot = SystemStateSnapshot(
        positions=[...],
        pending_orders=[...],
        account=...
    )
    persistence.save_state(snapshot)
    
    # Recover on startup
    coordinator = RecoveryCoordinator(
        persistence=persistence,
        broker=broker,
        position_store=position_store,
        order_machine=order_machine
    )
    
    report = coordinator.recover()
    if report.status == RecoveryStatus.SUCCESS:
        logger.info("Recovery successful")
    
    # Use resilient data provider
    provider = ResilientDataProvider(
        primary_provider=alpaca,
        fallback_providers=[polygon, finnhub]
    )
    
    quote = provider.get_quote("SPY")
    if quote.is_stale:
        logger.warning("Using stale data")
"""

# ============================================================================
# STATE PERSISTENCE
# ============================================================================

from core.recovery.persistence import (
    StatePersistence,
    SystemStateSnapshot,
    PositionSnapshot,
    OrderSnapshot,
    AccountSnapshot
)

# ============================================================================
# RECOVERY COORDINATION
# ============================================================================

from core.recovery.coordinator import (
    RecoveryCoordinator,
    RecoveryStatus,
    RecoveryReport
)

# ============================================================================
# GRACEFUL DEGRADATION
# ============================================================================

from core.recovery.degradation import (
    ResilientDataProvider,
    ProviderStatus,
    ProviderHealth,
    QuoteData
)


__all__ = [
    # State persistence
    "StatePersistence",
    "SystemStateSnapshot",
    "PositionSnapshot",
    "OrderSnapshot",
    "AccountSnapshot",
    
    # Recovery coordination
    "RecoveryCoordinator",
    "RecoveryStatus",
    "RecoveryReport",
    
    # Graceful degradation
    "ResilientDataProvider",
    "ProviderStatus",
    "ProviderHealth",
    "QuoteData",
]
