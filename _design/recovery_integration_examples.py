"""
Integration examples for recovery system.

Shows how to integrate StatePersistence, RecoveryCoordinator, and
ResilientDataProvider into the main trading runtime.
"""

from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import asyncio

from core.recovery import (
    StatePersistence,
    SystemStateSnapshot,
    PositionSnapshot,
    OrderSnapshot,
    AccountSnapshot,
    RecoveryCoordinator,
    RecoveryStatus,
    ResilientDataProvider
)


# ============================================================================
# EXAMPLE 1: STATE PERSISTENCE INTEGRATION
# ============================================================================

async def state_persistence_example():
    """
    Show how to integrate state persistence into runtime.
    
    PATTERN:
    1. Initialize persistence on startup
    2. Create snapshot from current state
    3. Save state every 60 seconds
    4. Load on next startup
    """
    
    # Initialize persistence
    persistence = StatePersistence(
        state_dir=Path("state"),
        backup_count=5,
        auto_backup=True
    )
    
    # State persistence loop (run in background)
    async def save_state_periodically():
        while True:
            try:
                # Get current state
                snapshot = create_state_snapshot()
                
                # Save to disk
                success = persistence.save_state(snapshot)
                
                if not success:
                    logger.error("Failed to save state")
                
            except Exception as e:
                logger.error(f"State persistence error: {e}")
            
            # Save every 60 seconds
            await asyncio.sleep(60)
    
    # Start background task
    asyncio.create_task(save_state_periodically())


def create_state_snapshot():
    """Create state snapshot from current system state."""
    
    # Get current positions
    positions = []
    for symbol, position in position_store.get_all_positions().items():
        positions.append(
            PositionSnapshot(
                symbol=symbol,
                quantity=position.quantity,
                avg_price=position.avg_price,
                entry_time=position.entry_time,
                unrealized_pnl=position.unrealized_pnl,
                side="LONG" if position.quantity > 0 else "SHORT"
            )
        )
    
    # Get pending orders
    pending_orders = []
    for order in order_machine.get_pending_orders():
        pending_orders.append(
            OrderSnapshot(
                order_id=order.order_id,
                broker_order_id=order.broker_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                status=order.status,
                submitted_at=order.submitted_at
            )
        )
    
    # Get account state
    account = broker.get_account()
    account_snapshot = AccountSnapshot(
        equity=Decimal(str(account.equity)),
        cash=Decimal(str(account.cash)),
        buying_power=Decimal(str(account.buying_power)),
        portfolio_value=Decimal(str(account.portfolio_value)),
        daily_pnl=Decimal(str(account.daily_pnl)),
        timestamp=datetime.now(timezone.utc)
    )
    
    # Create snapshot
    snapshot = SystemStateSnapshot(
        positions=positions,
        pending_orders=pending_orders,
        account=account_snapshot,
        session_start=session_start_time,
        trades_today=trade_counter,
        total_pnl_today=total_pnl,
        daily_loss_limit_remaining=risk_manager.get_remaining_loss_limit(),
        max_position_count=config.max_positions,
        current_position_count=len(positions)
    )
    
    return snapshot


# ============================================================================
# EXAMPLE 2: RECOVERY COORDINATOR INTEGRATION
# ============================================================================

def recovery_coordinator_example():
    """
    Show how to use RecoveryCoordinator on startup.
    
    PATTERN:
    1. Initialize coordinator with dependencies
    2. Call recover() on startup
    3. Handle recovery report
    4. Alert on issues
    5. Resume operation
    """
    
    # Initialize coordinator
    coordinator = RecoveryCoordinator(
        persistence=persistence,
        broker=broker,
        position_store=position_store,
        order_machine=order_machine,
        max_state_age_hours=24
    )
    
    # Recover on startup
    logger.info("Starting system recovery...")
    report = coordinator.recover()
    
    # Handle recovery status
    if report.status == RecoveryStatus.SUCCESS:
        logger.info("✅ Recovery successful", extra={
            "positions_recovered": report.positions_recovered,
            "orders_recovered": report.orders_recovered,
            "recovery_time": report.recovery_time_seconds
        })
    
    elif report.status == RecoveryStatus.PARTIAL:
        logger.warning("⚠️ Partial recovery - inconsistencies found", extra={
            "positions_recovered": report.positions_recovered,
            "positions_rebuilt": report.positions_rebuilt,
            "inconsistencies": report.inconsistencies_found
        })
        
        # Alert to Discord
        discord_notifier.send_error(
            error="Partial recovery on startup",
            details=f"Inconsistencies: {report.inconsistencies_found}"
        )
    
    elif report.status == RecoveryStatus.REBUILT:
        logger.info("🔄 State rebuilt from broker", extra={
            "positions_rebuilt": report.positions_rebuilt,
            "recovery_time": report.recovery_time_seconds
        })
        
        # This is OK - just means no saved state or stale
        discord_notifier.send_info(
            message="System started - state rebuilt from broker"
        )
    
    elif report.status == RecoveryStatus.FAILED:
        logger.error("❌ Recovery failed", extra={
            "inconsistencies": report.inconsistencies_found
        })
        
        # Critical alert
        discord_notifier.send_error(
            error="RECOVERY FAILED ON STARTUP",
            details=f"Errors: {report.inconsistencies_found}"
        )
        
        # Don't start trading
        raise RuntimeError("Recovery failed - manual intervention required")
    
    logger.info(f"Recovery complete: {report.status.value}")


# ============================================================================
# EXAMPLE 3: RESILIENT DATA PROVIDER INTEGRATION
# ============================================================================

def resilient_provider_example():
    """
    Show how to setup resilient data provider.
    
    PATTERN:
    1. Create primary provider (Alpaca)
    2. Create fallback providers (Polygon, Finnhub)
    3. Wrap in ResilientDataProvider
    4. Use as normal
    5. Monitor health
    """
    
    # Create providers
    alpaca_provider = AlpacaDataProvider(api_key=alpaca_key)
    polygon_provider = PolygonDataProvider(api_key=polygon_key)
    finnhub_provider = FinnhubDataProvider(api_key=finnhub_key)
    
    # Wrap in resilient provider
    data_provider = ResilientDataProvider(
        primary_provider=alpaca_provider,
        fallback_providers=[polygon_provider, finnhub_provider],
        staleness_threshold_seconds=60,  # 60s = stale
        cache_ttl_seconds=300,  # 5min cache
        max_retries=2  # 2 retries per provider
    )
    
    # Use as normal
    quote = data_provider.get_quote("SPY")
    
    if quote is None:
        logger.error("No data available for SPY")
        # Handle no data
    
    elif quote.is_stale:
        logger.warning(
            f"Using stale data for SPY",
            extra={
                "age_seconds": quote.age_seconds(),
                "provider": quote.provider
            }
        )
        # Maybe widen spreads or reduce size
    
    else:
        logger.debug(
            f"Got fresh data for SPY",
            extra={
                "last": str(quote.last),
                "provider": quote.provider,
                "latency_ms": quote.latency_ms
            }
        )
    
    # Monitor provider health
    health = data_provider.get_all_health()
    
    for provider_name, provider_health in health.items():
        if not provider_health.is_healthy():
            logger.warning(
                f"Provider {provider_name} unhealthy",
                extra={
                    "status": provider_health.status.value,
                    "consecutive_failures": provider_health.consecutive_failures,
                    "success_rate": provider_health.success_rate
                }
            )
            
            # Alert if primary is down
            if provider_name == "Alpaca":
                discord_notifier.send_error(
                    error="Primary data provider (Alpaca) unhealthy",
                    details=f"Status: {provider_health.status.value}"
                )


# ============================================================================
# EXAMPLE 4: COMPLETE STARTUP SEQUENCE
# ============================================================================

async def complete_startup_example():
    """
    Show complete startup sequence with all recovery components.
    
    STARTUP SEQUENCE:
    1. Initialize persistence
    2. Run recovery
    3. Setup resilient data provider
    4. Start state persistence loop
    5. Resume trading
    """
    
    logger.info("=== MiniQuantDesk Starting ===")
    
    # Step 1: Initialize persistence
    logger.info("Initializing state persistence...")
    persistence = StatePersistence(
        state_dir=Path("state"),
        backup_count=5
    )
    
    # Step 2: Run recovery
    logger.info("Running system recovery...")
    coordinator = RecoveryCoordinator(
        persistence=persistence,
        broker=broker,
        position_store=position_store,
        order_machine=order_machine
    )
    
    report = coordinator.recover()
    
    if report.status == RecoveryStatus.FAILED:
        logger.error("Recovery failed - shutting down")
        return
    
    logger.info(f"Recovery complete: {report.status.value}")
    
    # Step 3: Setup resilient data provider
    logger.info("Setting up data providers...")
    data_provider = ResilientDataProvider(
        primary_provider=alpaca_provider,
        fallback_providers=[polygon_provider, finnhub_provider]
    )
    
    # Step 4: Start state persistence loop
    logger.info("Starting state persistence loop...")
    
    async def persist_state():
        while True:
            snapshot = create_state_snapshot()
            persistence.save_state(snapshot)
            await asyncio.sleep(60)
    
    asyncio.create_task(persist_state())
    
    # Step 5: Start trading
    logger.info("Starting trading engine...")
    await trading_engine.start()
    
    logger.info("=== System fully operational ===")


# ============================================================================
# EXAMPLE 5: SHUTDOWN SEQUENCE
# ============================================================================

async def shutdown_example():
    """
    Show proper shutdown with state saving.
    
    SHUTDOWN SEQUENCE:
    1. Stop accepting new orders
    2. Wait for pending orders to complete
    3. Save final state
    4. Shutdown gracefully
    """
    
    logger.info("=== Shutting down ===")
    
    # Step 1: Stop new orders
    logger.info("Stopping new orders...")
    trading_engine.stop_accepting_orders()
    
    # Step 2: Wait for pending orders (max 60 seconds)
    logger.info("Waiting for pending orders...")
    timeout = 60
    start = datetime.now(timezone.utc)
    
    while order_machine.has_pending_orders():
        if (datetime.now(timezone.utc) - start).total_seconds() > timeout:
            logger.warning("Timeout waiting for orders - saving current state")
            break
        await asyncio.sleep(1)
    
    # Step 3: Save final state
    logger.info("Saving final state...")
    snapshot = create_state_snapshot()
    persistence.save_state(snapshot)
    
    # Step 4: Shutdown
    logger.info("Shutdown complete")


if __name__ == "__main__":
    # This file is for documentation only
    print("Integration examples for recovery system")
    print("See function docstrings for usage patterns")
