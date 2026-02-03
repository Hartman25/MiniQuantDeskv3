"""
P1 Patch 3 – Protective Stop Persistence Across Restarts (strict tests)

INVARIANT:
    After crash+restart, the runtime MUST reload protective stop orders
    from the broker via _load_protective_stops_from_broker() so that:
    1. It does not place duplicate protective stops for already-protected positions.
    2. It can cancel an existing protective stop when an exit signal fires.

These tests complement test_patch3_protective_stop_persistence.py with:
    - Integration: runtime loads stop IDs and passes them into the loop
    - Integration: SELL signal for a symbol with a known stop triggers cancel_order
    - Edge: symbols are uppercased consistently
    - _load_protective_stops_from_broker is unconditionally called in run()
"""

import pytest
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd


class TestProtectiveStopReloadIntegration:
    """Integration tests using the runtime harness."""

    def test_runtime_calls_load_protective_stops(self, monkeypatch, tmp_path):
        """_load_protective_stops_from_broker must be called during run()."""
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
            def get_bars(self, *a, **kw): return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self): return []
            def get_positions(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _StubBroker)

        load_calls = []
        orig_load = app_mod._load_protective_stops_from_broker

        def _spy(broker):
            load_calls.append(1)
            return orig_load(broker)

        monkeypatch.setattr(app_mod, "_load_protective_stops_from_broker", _spy)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)
        assert rc == 0
        assert len(load_calls) == 1, (
            "_load_protective_stops_from_broker must be called exactly once on startup"
        )

    def test_cancel_on_exit_with_loaded_stop(self, monkeypatch, tmp_path):
        """When a SELL signal fires for a symbol that has a loaded stop,
        exec_engine.cancel_order must be called with the stop's broker ID."""
        import core.runtime.app as app_mod
        from tests.conftest import FakeConfig, FakeContainer, _fake_df_to_contracts

        # Produce a SELL signal so the exit path is exercised
        sell_signal = {
            "action": "SELL",
            "symbol": "SPY",
            "quantity": 10,
            "side": "SELL",
            "strategy": "VWAPMicroMeanReversion",
        }
        cfg = FakeConfig(symbols=["SPY"])
        container = FakeContainer(cfg=cfg, signals=[sell_signal], position_qty=Decimal("10"))
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        class _StubBroker:
            def __init__(self, *a, **kw): pass
            def get_account_info(self):
                return {"portfolio_value": "100000", "buying_power": "50000"}
            def get_bars(self, *a, **kw): return pd.DataFrame()
            def get_orders(self): return []
            def list_open_orders(self):
                # Return a protective stop for SPY
                return [
                    SimpleNamespace(
                        id="BRK-STOP-SPY",
                        symbol="SPY",
                        side="sell",
                        order_type="stop",
                        status="accepted",
                    )
                ]
            def get_positions(self): return []

        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _StubBroker)

        opts = app_mod.RunOptions(
            config_path=tmp_path / "cfg.yaml",
            mode="paper", run_interval_s=0, run_once=True,
        )
        rc = app_mod.run(opts)

        # Check that cancel_order was called with the stop broker ID
        engine = container.get_execution_engine()
        cancel_calls = [
            (m, kw) for m, kw in engine.calls
            if m == "cancel_order" and "BRK-STOP-SPY" in str(kw.get("broker_order_id", ""))
        ]
        assert len(cancel_calls) >= 1, (
            f"cancel_order should have been called for BRK-STOP-SPY. "
            f"All calls: {engine.calls}"
        )


class TestLoadProtectiveStopsEdgeCases:
    """Additional edge cases for _load_protective_stops_from_broker."""

    def test_symbol_uppercased(self):
        """Symbols should be stored uppercased in the mapping."""
        from core.runtime.app import _load_protective_stops_from_broker

        orders = [
            SimpleNamespace(id="BRK-010", symbol="spy", side="sell",
                            order_type="stop", status="accepted"),
        ]

        class _Broker:
            def list_open_orders(self):
                return orders

        result = _load_protective_stops_from_broker(_Broker())
        assert "SPY" in result, f"Expected uppercase key 'SPY', got keys: {list(result.keys())}"

    def test_ignores_orders_without_symbol(self):
        """Orders missing symbol field are skipped gracefully."""
        from core.runtime.app import _load_protective_stops_from_broker

        orders = [
            SimpleNamespace(id="BRK-X", symbol="", side="sell",
                            order_type="stop", status="accepted"),
            {"id": "BRK-Y", "symbol": "", "side": "sell",
             "order_type": "stop"},
        ]

        class _Broker:
            def list_open_orders(self):
                return orders

        result = _load_protective_stops_from_broker(_Broker())
        assert result == {}, f"Should be empty for missing symbols, got: {result}"

    def test_ignores_orders_without_id(self):
        """Orders missing id field are skipped gracefully."""
        from core.runtime.app import _load_protective_stops_from_broker

        orders = [
            SimpleNamespace(id="", symbol="AAPL", side="sell",
                            order_type="stop", status="accepted"),
        ]

        class _Broker:
            def list_open_orders(self):
                return orders

        result = _load_protective_stops_from_broker(_Broker())
        assert result == {}, f"Should be empty for missing ids, got: {result}"
