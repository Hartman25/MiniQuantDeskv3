"""
Tests for monitoring system.

COVERAGE:
- HealthChecker functionality
- ExecutionMonitor metrics
- DriftDetector accuracy
- Integration scenarios

DESIGN:
- Unit tests for each component
- Integration tests for workflow
- Mock external dependencies (broker, position store)
- Real-world scenario testing
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch

from core.monitoring import (
    HealthChecker,
    HealthStatus,
    ComponentType,
    ExecutionMonitor,
    OrderStatus,
    DriftDetector,
    DriftType,
    DriftSeverity,
    PositionState
)


# ============================================================================
# HEALTH CHECKER TESTS
# ============================================================================

class TestHealthChecker:
    """Test HealthChecker functionality."""
    
    def test_health_checker_initialization(self):
        """Test health checker initializes correctly."""
        checker = HealthChecker(check_interval=30)
        
        assert checker.check_interval == 30
        assert not checker._running
        assert len(checker._checks) == 0
    
    def test_register_health_check(self):
        """Test registering a health check."""
        checker = HealthChecker()
        
        def check_func():
            from core.monitoring.health import HealthCheckResult
            return HealthCheckResult(
                component=ComponentType.BROKER,
                status=HealthStatus.HEALTHY,
                message="OK",
                timestamp=datetime.now(timezone.utc)
            )
        
        checker.register_check(
            ComponentType.BROKER,
            check_function=check_func,
            interval_seconds=30
        )
        
        assert ComponentType.BROKER in checker._checks
        assert checker._checks[ComponentType.BROKER].interval_seconds == 30
    
    def test_unregister_health_check(self):
        """Test unregistering a health check."""
        checker = HealthChecker()
        
        def check_func():
            from core.monitoring.health import HealthCheckResult
            return HealthCheckResult(
                component=ComponentType.BROKER,
                status=HealthStatus.HEALTHY,
                message="OK",
                timestamp=datetime.now(timezone.utc)
            )
        
        checker.register_check(ComponentType.BROKER, check_func)
        checker.unregister_check(ComponentType.BROKER)
        
        assert ComponentType.BROKER not in checker._checks
    
    def test_disk_space_check(self):
        """Test built-in disk space check."""
        from pathlib import Path
        from core.monitoring.health import SystemResourceChecker
        
        result = SystemResourceChecker.check_disk_space(
            Path("/"),
            warning_threshold_pct=80.0,
            critical_threshold_pct=90.0
        )
        
        assert result.component == ComponentType.DISK_SPACE
        assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
        assert "used_pct" in result.metrics
        assert result.metrics["used_pct"] >= 0
        assert result.metrics["used_pct"] <= 100
    
    def test_memory_check(self):
        """Test built-in memory check."""
        from core.monitoring.health import SystemResourceChecker
        
        result = SystemResourceChecker.check_memory_usage(
            warning_threshold_pct=80.0,
            critical_threshold_pct=90.0
        )
        
        assert result.component == ComponentType.MEMORY
        assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
        assert "used_pct" in result.metrics
        assert result.metrics["used_pct"] >= 0
        assert result.metrics["used_pct"] <= 100
    
    def test_system_health_aggregation(self):
        """Test system health aggregation."""
        checker = HealthChecker()
        
        # Register multiple checks
        def healthy_check():
            from core.monitoring.health import HealthCheckResult
            return HealthCheckResult(
                component=ComponentType.BROKER,
                status=HealthStatus.HEALTHY,
                message="OK",
                timestamp=datetime.now(timezone.utc)
            )
        
        def degraded_check():
            from core.monitoring.health import HealthCheckResult
            return HealthCheckResult(
                component=ComponentType.DATA_FEED,
                status=HealthStatus.DEGRADED,
                message="Slow",
                timestamp=datetime.now(timezone.utc)
            )
        
        checker.register_check(ComponentType.BROKER, healthy_check, interval_seconds=1)
        checker.register_check(ComponentType.DATA_FEED, degraded_check, interval_seconds=1)
        
        # Manually run checks
        checker._run_checks()
        
        # Get system health
        system_health = checker.get_system_health()
        
        assert system_health.overall_status == HealthStatus.DEGRADED  # One degraded component
        assert len(system_health.components) == 2
        assert HealthStatus.HEALTHY in system_health.status_counts
        assert HealthStatus.DEGRADED in system_health.status_counts


# ============================================================================
# EXECUTION MONITOR TESTS
# ============================================================================

class TestExecutionMonitor:
    """Test ExecutionMonitor functionality."""
    
    def test_execution_monitor_initialization(self):
        """Test execution monitor initializes correctly."""
        monitor = ExecutionMonitor(max_history=1000)
        
        assert monitor.max_history == 1000
        assert len(monitor._executions) == 0
        assert len(monitor._pending) == 0
    
    def test_record_submission_and_fill(self):
        """Test recording order submission and fill."""
        monitor = ExecutionMonitor()
        
        # Record submission
        monitor.record_submission(
            order_id="ORDER_123",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("10"),
            expected_price=Decimal("600.00")
        )
        
        assert "ORDER_123" in monitor._pending
        assert monitor._pending["ORDER_123"].symbol == "SPY"
        
        # Record fill
        monitor.record_fill(
            order_id="ORDER_123",
            fill_price=Decimal("600.05")
        )
        
        assert "ORDER_123" not in monitor._pending
        assert len(monitor._executions) == 1
        
        execution = monitor._executions[0]
        assert execution.order_id == "ORDER_123"
        assert execution.fill_price == Decimal("600.05")
        assert execution.status == OrderStatus.FILLED
        assert execution.slippage_bps is not None
    
    def test_slippage_calculation_buy_order(self):
        """Test slippage calculation for buy order."""
        monitor = ExecutionMonitor()
        
        # Buy order with positive slippage (paid more than expected - bad)
        monitor.record_submission(
            order_id="BUY_1",
            symbol="SPY",
            side="BUY",
            quantity=Decimal("10"),
            expected_price=Decimal("100.00")
        )
        
        monitor.record_fill(
            order_id="BUY_1",
            fill_price=Decimal("100.10")  # Paid 10 cents more
        )
        
        execution = monitor._executions[0]
        # Slippage = (100.10 - 100.00) / 100.00 * 10000 = 10 bps
        assert execution.slippage_bps == Decimal("10")
    
    def test_slippage_calculation_sell_order(self):
        """Test slippage calculation for sell order."""
        monitor = ExecutionMonitor()
        
        # Sell order with positive slippage (received less than expected - bad)
        monitor.record_submission(
            order_id="SELL_1",
            symbol="SPY",
            side="SELL",
            quantity=Decimal("10"),
            expected_price=Decimal("100.00")
        )
        
        monitor.record_fill(
            order_id="SELL_1",
            fill_price=Decimal("99.90")  # Received 10 cents less
        )
        
        execution = monitor._executions[0]
        # For sell orders: slippage sign is inverted
        # (99.90 - 100.00) / 100.00 * 10000 = -10 bps → inverted to +10 bps
        assert execution.slippage_bps == Decimal("10")
    
    def test_get_summary(self):
        """Test execution summary calculation."""
        monitor = ExecutionMonitor()
        
        # Submit 5 orders
        for i in range(5):
            monitor.record_submission(
                order_id=f"ORDER_{i}",
                symbol="SPY",
                side="BUY",
                quantity=Decimal("10"),
                expected_price=Decimal("600.00")
            )
        
        # Fill 4 orders
        for i in range(4):
            monitor.record_fill(
                order_id=f"ORDER_{i}",
                fill_price=Decimal("600.05")
            )
        
        # Reject 1 order
        monitor.record_rejection(order_id="ORDER_4", reason="Insufficient margin")
        
        summary = monitor.get_summary(lookback_minutes=60)
        
        assert summary.total_orders == 5
        assert summary.filled_orders == 4
        assert summary.rejected_orders == 1
        assert summary.fill_rate == 0.8  # 4/5
    
    def test_detect_anomalies_low_fill_rate(self):
        """Test anomaly detection for low fill rate."""
        monitor = ExecutionMonitor(fill_rate_threshold=0.90)
        
        # Submit 10 orders
        for i in range(10):
            monitor.record_submission(
                order_id=f"ORDER_{i}",
                symbol="SPY",
                side="BUY",
                quantity=Decimal("10"),
                expected_price=Decimal("600.00")
            )
        
        # Fill only 7 orders (70% fill rate)
        for i in range(7):
            monitor.record_fill(
                order_id=f"ORDER_{i}",
                fill_price=Decimal("600.05")
            )
        
        # Reject 3 orders
        for i in range(7, 10):
            monitor.record_rejection(order_id=f"ORDER_{i}", reason="Rejected")
        
        alerts = monitor.detect_anomalies(lookback_minutes=60)
        
        assert len(alerts) > 0
        assert any("fill rate" in alert[1].lower() for alert in alerts)
    
    def test_detect_anomalies_high_slippage(self):
        """Test anomaly detection for high slippage."""
        monitor = ExecutionMonitor(avg_slippage_threshold_bps=10.0)
        
        # Submit and fill orders with high slippage
        for i in range(5):
            monitor.record_submission(
                order_id=f"ORDER_{i}",
                symbol="SPY",
                side="BUY",
                quantity=Decimal("10"),
                expected_price=Decimal("100.00")
            )
            
            # High slippage: 20 cents = 20 bps
            monitor.record_fill(
                order_id=f"ORDER_{i}",
                fill_price=Decimal("100.20")
            )
        
        alerts = monitor.detect_anomalies(lookback_minutes=60)
        
        assert len(alerts) > 0
        assert any("slippage" in alert[1].lower() for alert in alerts)


# ============================================================================
# DRIFT DETECTOR TESTS
# ============================================================================

class TestDriftDetector:
    """Test DriftDetector functionality."""
    
    def test_drift_detector_initialization(self):
        """Test drift detector initializes correctly."""
        position_store = Mock()
        broker = Mock()
        
        detector = DriftDetector(
            position_store=position_store,
            broker=broker,
            check_interval_seconds=60
        )
        
        assert detector.position_store == position_store
        assert detector.broker == broker
        assert detector.check_interval == 60
    
    def test_no_drift_when_positions_match(self):
        """Test no drift detected when positions match."""
        position_store = Mock()
        broker = Mock()
        
        # Mock local position
        local_position = Mock()
        local_position.quantity = Decimal("10")
        local_position.avg_price = Decimal("100.00")
        position_store.get_symbols.return_value = ["SPY"]
        position_store.get_position.return_value = local_position
        
        # Mock broker position
        broker_position = Mock()
        broker_position.symbol = "SPY"
        broker_position.qty = "10"
        broker_position.avg_entry_price = "100.00"
        broker_position.side = "LONG"
        broker.get_positions.return_value = [broker_position]
        
        detector = DriftDetector(position_store, broker)
        drifts = detector.check_drift()
        
        assert len(drifts) == 0  # No drift
    
    def test_quantity_mismatch_detected(self):
        """Test quantity mismatch drift detection."""
        position_store = Mock()
        broker = Mock()
        
        # Local has 10 shares
        local_position = Mock()
        local_position.quantity = Decimal("10")
        local_position.avg_price = Decimal("100.00")
        position_store.get_symbols.return_value = ["SPY"]
        position_store.get_position.return_value = local_position
        
        # Broker has 12 shares (2 share drift)
        broker_position = Mock()
        broker_position.symbol = "SPY"
        broker_position.qty = "12"
        broker_position.avg_entry_price = "100.00"
        broker_position.side = "LONG"
        broker.get_positions.return_value = [broker_position]
        
        detector = DriftDetector(
            position_store,
            broker,
            minor_quantity_threshold=Decimal("1"),
            moderate_quantity_threshold=Decimal("5")
        )
        
        drifts = detector.check_drift()
        
        assert len(drifts) == 1
        assert drifts[0].drift_type == DriftType.QUANTITY_MISMATCH
        assert drifts[0].quantity_delta == Decimal("2")
        assert drifts[0].severity == DriftSeverity.MODERATE
    
    def test_unknown_position_detected(self):
        """Test unknown position (broker has, we don't)."""
        position_store = Mock()
        broker = Mock()
        
        # Local has no positions
        position_store.get_symbols.return_value = []
        
        # Broker has position
        broker_position = Mock()
        broker_position.symbol = "AAPL"
        broker_position.qty = "10"
        broker_position.avg_entry_price = "150.00"
        broker_position.side = "LONG"
        broker.get_positions.return_value = [broker_position]
        
        detector = DriftDetector(position_store, broker)
        drifts = detector.check_drift()
        
        assert len(drifts) == 1
        assert drifts[0].drift_type == DriftType.UNKNOWN_POSITION
        assert drifts[0].local_state is None
        assert drifts[0].broker_state is not None
    
    def test_ghost_position_detected(self):
        """Test ghost position (we have, broker doesn't)."""
        position_store = Mock()
        broker = Mock()
        
        # Local has position
        local_position = Mock()
        local_position.quantity = Decimal("10")
        local_position.avg_price = Decimal("100.00")
        position_store.get_symbols.return_value = ["SPY"]
        position_store.get_position.return_value = local_position
        
        # Broker has no positions
        broker.get_positions.return_value = []
        
        detector = DriftDetector(position_store, broker)
        drifts = detector.check_drift()
        
        assert len(drifts) == 1
        assert drifts[0].drift_type == DriftType.GHOST_POSITION
        assert drifts[0].local_state is not None
        assert drifts[0].broker_state is None
    
    def test_auto_reconcile_minor_drift(self):
        """Test automatic reconciliation of minor drift."""
        position_store = Mock()
        broker = Mock()
        
        # Create minor drift
        local_state = PositionState(
            symbol="SPY",
            quantity=Decimal("10"),
            avg_price=Decimal("100.00"),
            side="LONG",
            timestamp=datetime.now(timezone.utc),
            source="LOCAL"
        )
        
        broker_state = PositionState(
            symbol="SPY",
            quantity=Decimal("10.5"),  # 0.5 share drift (minor)
            avg_price=Decimal("100.00"),
            side="LONG",
            timestamp=datetime.now(timezone.utc),
            source="BROKER"
        )
        
        from core.monitoring.drift import PositionDrift
        drift = PositionDrift(
            symbol="SPY",
            drift_type=DriftType.QUANTITY_MISMATCH,
            severity=DriftSeverity.MINOR,
            local_state=local_state,
            broker_state=broker_state,
            detected_at=datetime.now(timezone.utc)
        )
        
        position_store.sync_position = Mock()
        
        detector = DriftDetector(position_store, broker)
        result = detector.auto_reconcile(drift)
        
        assert result is True
        assert position_store.sync_position.called


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestMonitoringIntegration:
    """Test monitoring components working together."""
    
    def test_health_checks_with_execution_monitor(self):
        """Test health checker monitoring execution quality."""
        # This would test the integration between components
        # For now, just verify components can be initialized together
        
        execution_monitor = ExecutionMonitor()
        health_checker = HealthChecker()
        
        # Register execution health check
        def check_execution_health():
            from core.monitoring.health import HealthCheckResult
            
            summary = execution_monitor.get_summary(lookback_minutes=60)
            
            if summary.fill_rate < 0.9:
                status = HealthStatus.DEGRADED
                message = f"Low fill rate: {summary.fill_rate:.1%}"
            else:
                status = HealthStatus.HEALTHY
                message = f"Execution OK: {summary.fill_rate:.1%} fill rate"
            
            return HealthCheckResult(
                component=ComponentType.ORDER_MACHINE,
                status=status,
                message=message,
                timestamp=datetime.now(timezone.utc),
                metrics={"fill_rate": summary.fill_rate}
            )
        
        health_checker.register_check(
            ComponentType.ORDER_MACHINE,
            check_execution_health
        )
        
        assert ComponentType.ORDER_MACHINE in health_checker._checks


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
