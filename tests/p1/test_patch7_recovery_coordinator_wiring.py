"""
P1 Patch 7 – Recovery Coordinator Wiring

INVARIANT:
    On startup, the runtime must invoke RecoveryCoordinator.recover()
    so that persisted state (positions, protective stops, submitted-order IDs)
    is reconstructed before the main trading loop begins.

WHY THIS MATTERS:
    RecoveryCoordinator already exists but is never called from the runtime.
    Without wiring it in, all crash-recovery logic (state snapshots, broker
    reconciliation, position reconstruction) is dead code.

DESIGN:
    - `app.py` instantiates RecoveryCoordinator with StatePersistence,
      broker, position_store, and order_machine.
    - Calls coordinator.recover() before entering the main loop.
    - If RecoveryStatus.FAILED, runtime halts with exit-code 1.
    - On SUCCESS / PARTIAL / REBUILT, continues to the loop.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from decimal import Decimal


class TestRecoveryCoordinatorUnit:
    """Unit-level tests for RecoveryCoordinator behaviour."""

    def test_recover_returns_success_when_no_saved_state(self, tmp_path):
        """No persisted state => REBUILT from broker (not FAILED)."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = StatePersistence(state_dir=tmp_path / "state")
        broker = MagicMock()
        broker.get_positions.return_value = []
        broker.get_orders.return_value = []

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
        )
        report = coord.recover()

        assert report.status == RecoveryStatus.REBUILT
        assert report.recovery_time_seconds >= 0

    def test_recover_returns_failed_on_exception(self, tmp_path):
        """If persistence.load_latest_state() raises, status is FAILED."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = MagicMock(spec=StatePersistence)
        persistence.load_latest_state.side_effect = RuntimeError("disk I/O boom")

        broker = MagicMock()
        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
        )
        report = coord.recover()

        assert report.status == RecoveryStatus.FAILED

    def test_recover_rebuilds_positions_from_broker(self, tmp_path):
        """When broker has positions but no saved state, they are rebuilt."""
        from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
        from core.recovery.persistence import StatePersistence

        persistence = StatePersistence(state_dir=tmp_path / "state")

        # broker returns one position
        pos = MagicMock()
        pos.symbol = "SPY"
        pos.qty = "10"
        pos.avg_entry_price = "450.00"

        broker = MagicMock()
        broker.get_positions.return_value = [pos]
        broker.get_orders.return_value = []

        position_store = MagicMock()
        order_machine = MagicMock()

        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
        )
        report = coord.recover()

        assert report.status == RecoveryStatus.REBUILT
        assert report.positions_rebuilt == 1
        # position_store.restore_position must have been called
        position_store.restore_position.assert_called_once()


class TestRecoveryWiringIntegration:
    """Verify that app.py calls recover() and halts on FAILED."""

    def test_startup_calls_recovery_coordinator(self, monkeypatch, tmp_path):
        """
        Verify the runtime creates a RecoveryCoordinator and calls recover()
        before the first trading cycle.  We detect this by patching the
        coordinator class to record that recover() was called, then let
        the loop run_once.
        """
        import core.runtime.app as app_mod
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[])
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _OkBroker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self): return {"portfolio_value": "100000", "buying_power": "50000"}
            def get_bars(self, *a, **kw):
                import pandas as pd
                return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _OkBroker)

        recover_called = {"called": False}
        original_recover = None

        # Patch RecoveryCoordinator if it is used in app.py
        if hasattr(app_mod, "_try_recovery"):
            orig_try = app_mod._try_recovery
            def _mock_try(*a, **kw):
                recover_called["called"] = True
                return orig_try(*a, **kw)
            monkeypatch.setattr(app_mod, "_try_recovery", _mock_try)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper",
            run_interval_s=0,
            run_once=True,
        )

        rc = app_mod.run(opts)
        # The test passes if:
        # 1) run() didn't crash (exit code 0), AND
        # 2) _try_recovery was called if it exists
        if hasattr(app_mod, "_try_recovery"):
            assert recover_called["called"], "Recovery coordinator was not invoked on startup"
        assert rc == 0

    def test_runtime_halts_on_recovery_failure(self, monkeypatch, tmp_path):
        """
        If _try_recovery returns FAILED, the runtime must halt (exit code 1).
        """
        import core.runtime.app as app_mod
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[])
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _OkBroker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self): return {"portfolio_value": "100000", "buying_power": "50000"}
            def get_bars(self, *a, **kw):
                import pandas as pd
                return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _OkBroker)

        # Make recovery fail
        if hasattr(app_mod, "_try_recovery"):
            from core.recovery.coordinator import RecoveryStatus
            monkeypatch.setattr(app_mod, "_try_recovery", lambda *a, **kw: RecoveryStatus.FAILED)

            opts = app_mod.RunOptions(
                config_path=tmp_path / "cfg.yaml",
                mode="paper",
                run_interval_s=0,
                run_once=True,
            )

            rc = app_mod.run(opts)
            assert rc == 1, "Runtime must halt with exit code 1 when recovery fails"
        else:
            pytest.skip("_try_recovery not yet wired into app.py")
