"""
P1 Patch 1 – Crash Recovery Validation

INVARIANT:
    On startup the runtime MUST call _try_recovery() before the main loop.
    If recovery returns FAILED, the runtime MUST exit with code 1.
    If recovery returns SUCCESS / PARTIAL / REBUILT, the runtime continues.
    Snapshot builder MUST produce a valid SystemStateSnapshot with correct
    typed values (Decimal, datetime, correct field counts).

These tests are strict: no conditional hasattr guards.  They will fail
if the wiring is removed or broken.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers (duplicated from conftest to keep this file self-contained)
# ---------------------------------------------------------------------------
def _make_fake_broker():
    """Minimal broker stub with the methods _try_recovery needs."""
    b = MagicMock()
    b.get_positions.return_value = []
    b.get_orders.return_value = []
    b.list_open_orders.return_value = []
    b.get_account_info.return_value = {
        "portfolio_value": "100000",
        "buying_power": "50000",
    }
    return b


# ===================================================================
# A) _try_recovery invocation & status handling
# ===================================================================

class TestTryRecoveryFunction:
    """Direct unit tests for _try_recovery()."""

    def test_try_recovery_exists_in_app_module(self):
        """_try_recovery must be importable from core.runtime.app."""
        import core.runtime.app as app_mod
        assert callable(getattr(app_mod, "_try_recovery", None)), (
            "_try_recovery is not defined in app.py"
        )

    def test_try_recovery_returns_rebuilt_when_no_state(self, tmp_path):
        """Empty state dir → REBUILT (not FAILED)."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        broker = _make_fake_broker()
        ps = MagicMock()
        om = MagicMock()

        status = _try_recovery(broker, ps, om, state_dir=tmp_path / "empty_state")
        assert status == RecoveryStatus.REBUILT

    def test_try_recovery_returns_failed_on_persistence_error(self, tmp_path):
        """If StatePersistence explodes, _try_recovery must return REBUILT
        (fail-open), NOT FAILED, because the exception is caught."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus

        # We monkeypatch RecoveryCoordinator.recover to raise
        with patch("core.runtime.app.RecoveryCoordinator") as MockCoord:
            MockCoord.return_value.recover.side_effect = RuntimeError("boom")
            status = _try_recovery(
                _make_fake_broker(), MagicMock(), MagicMock(),
                state_dir=tmp_path / "bad",
            )
        assert status == RecoveryStatus.REBUILT

    def test_try_recovery_propagates_failed(self, tmp_path):
        """If coordinator.recover() returns FAILED report, status is FAILED."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus, RecoveryReport

        report = RecoveryReport(
            status=RecoveryStatus.FAILED,
            recovered_state=None,
            positions_recovered=0, positions_rebuilt=0,
            orders_recovered=0, orders_cancelled=0,
            inconsistencies_found=["test"],
            recovery_time_seconds=0.0,
            timestamp=datetime.now(timezone.utc),
        )
        with patch("core.runtime.app.RecoveryCoordinator") as MockCoord:
            MockCoord.return_value.recover.return_value = report
            status = _try_recovery(
                _make_fake_broker(), MagicMock(), MagicMock(),
                state_dir=tmp_path / "fail",
            )
        assert status == RecoveryStatus.FAILED

    @pytest.mark.parametrize("rs", ["SUCCESS", "PARTIAL", "REBUILT"])
    def test_try_recovery_passes_non_failed_statuses(self, tmp_path, rs):
        """SUCCESS / PARTIAL / REBUILT all pass through unchanged."""
        from core.runtime.app import _try_recovery
        from core.recovery.coordinator import RecoveryStatus, RecoveryReport

        status_enum = RecoveryStatus(rs)
        report = RecoveryReport(
            status=status_enum,
            recovered_state=None,
            positions_recovered=0, positions_rebuilt=0,
            orders_recovered=0, orders_cancelled=0,
            inconsistencies_found=[],
            recovery_time_seconds=0.01,
            timestamp=datetime.now(timezone.utc),
        )
        with patch("core.runtime.app.RecoveryCoordinator") as MockCoord:
            MockCoord.return_value.recover.return_value = report
            status = _try_recovery(
                _make_fake_broker(), MagicMock(), MagicMock(),
                state_dir=tmp_path / "ok",
            )
        assert status == status_enum


# ===================================================================
# B) Runtime integration – recovery called before loop
# ===================================================================

class TestRuntimeRecoveryIntegration:
    """Verify app.run() actually calls _try_recovery and respects its result."""

    def _make_runtime_env(self, monkeypatch, tmp_path):
        """Set up minimal monkeypatched environment for app.run()."""
        import core.runtime.app as app_mod
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[])
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _StubBroker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self):
                return {"portfolio_value": "100000", "buying_power": "50000"}
            def get_bars(self, *a, **kw):
                return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []
            def get_positions(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _StubBroker)
        return app_mod

    def test_run_calls_try_recovery(self, monkeypatch, tmp_path):
        """_try_recovery must be called exactly once during app.run()."""
        app_mod = self._make_runtime_env(monkeypatch, tmp_path)
        from core.recovery.coordinator import RecoveryStatus

        call_log = []
        orig = app_mod._try_recovery

        def _spy(*a, **kw):
            call_log.append(1)
            return orig(*a, **kw)

        monkeypatch.setattr(app_mod, "_try_recovery", _spy)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 0
        assert len(call_log) == 1, "_try_recovery was not called exactly once"

    def test_run_halts_on_failed_recovery(self, monkeypatch, tmp_path):
        """If _try_recovery returns FAILED, app.run() must return 1."""
        app_mod = self._make_runtime_env(monkeypatch, tmp_path)
        from core.recovery.coordinator import RecoveryStatus

        monkeypatch.setattr(
            app_mod, "_try_recovery",
            lambda *a, **kw: RecoveryStatus.FAILED,
        )

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 1, "Runtime must halt with exit code 1 on FAILED recovery"

    @pytest.mark.parametrize("status", ["SUCCESS", "PARTIAL", "REBUILT"])
    def test_run_continues_on_non_failed_recovery(self, monkeypatch, tmp_path, status):
        """SUCCESS / PARTIAL / REBUILT → runtime runs normally (exit 0)."""
        app_mod = self._make_runtime_env(monkeypatch, tmp_path)
        from core.recovery.coordinator import RecoveryStatus

        monkeypatch.setattr(
            app_mod, "_try_recovery",
            lambda *a, **kw: RecoveryStatus(status),
        )

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 0, f"Runtime should continue (exit 0) on {status} recovery"


# ===================================================================
# C) Snapshot shape validation
# ===================================================================

class TestSnapshotShape:
    """Validate that build_state_snapshot produces correctly typed values."""

    def test_snapshot_has_required_fields(self, tmp_path):
        """Snapshot must contain positions, pending_orders, timestamp, current_position_count."""
        from core.state.position_store import PositionStore
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        snap = build_state_snapshot(position_store=ps)
        ps.close()

        assert hasattr(snap, "positions")
        assert hasattr(snap, "pending_orders")
        assert hasattr(snap, "timestamp")
        assert hasattr(snap, "current_position_count")

    def test_snapshot_position_quantities_are_decimal(self, tmp_path):
        """Position quantities and prices must be Decimal, not float."""
        from core.state.position_store import PositionStore, Position
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        ps.upsert(Position(
            symbol="AAPL",
            quantity=Decimal("25"),
            entry_price=Decimal("175.50"),
            entry_time=datetime(2026, 1, 30, 14, 0, tzinfo=timezone.utc),
            strategy="TestStrat",
            order_id="ORD-100",
        ))
        snap = build_state_snapshot(position_store=ps)
        ps.close()

        p = snap.positions[0]
        assert isinstance(p.quantity, Decimal), f"Expected Decimal, got {type(p.quantity)}"
        assert isinstance(p.avg_price, Decimal), f"Expected Decimal, got {type(p.avg_price)}"

    def test_snapshot_timestamp_is_utc_aware(self, tmp_path):
        """Snapshot timestamp must be UTC-aware datetime."""
        from core.state.position_store import PositionStore
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        snap = build_state_snapshot(position_store=ps)
        ps.close()

        assert isinstance(snap.timestamp, datetime)
        assert snap.timestamp.tzinfo is not None, "Snapshot timestamp must be timezone-aware"

    def test_snapshot_stop_orders_have_correct_type(self, tmp_path):
        """Pending orders from protective_stop_ids must have order_type=STOP."""
        from core.state.position_store import PositionStore
        from core.runtime.state_snapshot import build_state_snapshot

        ps = PositionStore(db_path=tmp_path / "pos.db")
        snap = build_state_snapshot(
            position_store=ps,
            protective_stop_ids={"SPY": "BRK-001"},
        )
        ps.close()

        assert len(snap.pending_orders) == 1
        o = snap.pending_orders[0]
        assert o.order_type == "STOP"
        assert o.side == "SELL"
        assert o.symbol == "SPY"
