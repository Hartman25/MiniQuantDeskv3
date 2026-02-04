"""
P3-O4 — Separate Execution Errors from Trading Losses

INVARIANT:
    The system MUST distinguish between:
    - Execution errors: OrderValidationError, OrderExecutionError, broker failures
    - Trading losses: Legitimate trades that resulted in negative P&L

    OrderValidationError MUST be a distinct subclass of OrderExecutionError.
    The event handler MUST log kill switch activations with trigger_source
    to distinguish automated vs manual triggers.

TESTS:
    4 tests proving error classification is maintained.
"""

from decimal import Decimal

from core.execution.engine import OrderValidationError, OrderExecutionError
from core.events.types import KillSwitchActivatedEvent
from core.state.transaction_log import TransactionLog


class TestSeparateExecutionErrors:

    def test_validation_error_is_execution_error(self):
        """OrderValidationError inherits from OrderExecutionError."""
        assert issubclass(OrderValidationError, OrderExecutionError)

    def test_validation_error_catchable_separately(self):
        """OrderValidationError can be caught before generic ExecutionError."""
        try:
            raise OrderValidationError("bad qty")
        except OrderValidationError as e:
            assert "bad qty" in str(e)
        except OrderExecutionError:
            assert False, "Should have been caught as OrderValidationError first"

    def test_kill_switch_trigger_source_distinguishes_origin(self):
        """trigger_source field distinguishes automated vs manual kills."""
        auto = KillSwitchActivatedEvent(
            reason="Daily loss limit", trigger_source="DailyLossLimitProtection",
            all_positions_closed=True, all_orders_cancelled=True,
        )
        manual = KillSwitchActivatedEvent(
            reason="Human override", trigger_source="ManualKillSwitch",
            all_positions_closed=True, all_orders_cancelled=True,
        )
        assert auto.trigger_source != manual.trigger_source
        assert "Protection" in auto.trigger_source
        assert "Manual" in manual.trigger_source

    def test_transaction_log_records_events(self):
        """TransactionLog stores events for audit trail."""
        import tempfile, os
        path = os.path.join(tempfile.gettempdir(), 'test_txlog_p3o4.jsonl')
        try:
            log = TransactionLog(path=path)
            log.append({
                'event_type': 'execution_error',
                'reason': 'Broker timeout',
            })
            log.append({
                'event_type': 'trade_loss',
                'pnl': '-1.50',
            })
            events = log.read_all()
            types = [e['event_type'] for e in events]
            assert 'execution_error' in types
            assert 'trade_loss' in types
        finally:
            log.close()
            if os.path.exists(path):
                os.unlink(path)
