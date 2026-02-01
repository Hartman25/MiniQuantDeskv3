from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest


# ----------------------------
# Fakes that match app.run()
# ----------------------------

class FakeBrokerConnector:
    """Replaces AlpacaBrokerConnector: no network, just minimal surface."""
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper

    def get_account_info(self) -> Dict[str, Any]:
        return {
            "paper": self.paper,
            "buying_power": "100000",
            "cash": "100000",
            "portfolio_value": "100000",
        }

    def get_bars(self, symbol: str, timeframe: str, limit: int = 1) -> pd.DataFrame:
        """Return minimal DataFrame for _df_to_contracts."""
        idx = pd.date_range(end=pd.Timestamp.now("UTC"), periods=1, freq="1min")
        return pd.DataFrame(
            {
                "open": [100.0],
                "high": [100.0],
                "low": [100.0],
                "close": [100.0],
                "volume": [1000],
            },
            index=idx,
        )

    def get_order_status(self, broker_order_id: str):
        """Return filled status."""
        from core.state import OrderStatus
        return (OrderStatus.FILLED, {"filled_qty": Decimal("1"), "filled_avg_price": Decimal("100")})

    def get_position(self, symbol: str):
        """Return no position."""
        return None


class FakeProtections:
    """Minimal protections that never block."""
    def check(self, symbol=None, current_trades=None, completed_trades=None):
        return SimpleNamespace(is_protected=False, reason=None, until=None)


class FakeDataValidator:
    """Minimal validator that never rejects."""
    def validate_bars(self, bars, timeframe):
        return True


class FakeOrderTracker:
    """Minimal order tracker."""
    def get_orphaned_orders(self, broker_orders):
        return []

    def get_shadow_orders(self, broker_orders):
        return []


class FakeLifecycle:
    """
    app.run uses:
      - add_strategy(strategy)
      - start_strategy(name)
      - on_bar(bar) -> list[dict]
      - on_order_filled(...)  # optional callback
    """
    def __init__(self, signals: List[Dict[str, Any]]):
        self._queue = list(signals)
        self._emitted = False

    def add_strategy(self, _strategy: Any) -> None:
        return

    def start_strategy(self, _name: str) -> None:
        return

    def on_bar(self, bar: Any) -> List[Dict[str, Any]]:
        """Emit signals once, then empty forever."""
        if self._emitted:
            return []
        self._emitted = True
        return list(self._queue)

    def on_order_filled(self, strategy_name, order_id, symbol, filled_qty, fill_price):
        return


class FakeStrategyRegistry:
    """Registry that accepts registrations but does nothing."""
    def register(self, _cls: Any) -> None:
        return

    def create(self, name: str, config: Dict, symbols: List[str], timeframe: str):
        return SimpleNamespace(name=name, symbols=symbols, timeframe=timeframe, config=config)


class FakePositionStore:
    """Minimal position store."""
    def get(self, _symbol: str) -> Optional[Any]:
        return None

    def get_position(self, _symbol: str) -> Optional[Any]:
        return None

    def upsert(self, position: Any) -> None:
        return

    def delete(self, _symbol: str) -> None:
        return


class FakeRiskManager:
    """Risk manager that approves everything."""
    def validate_trade(self, symbol, quantity, side, price, account_value, buying_power, strategy):
        return SimpleNamespace(approved=True, reason=None, to_dict=lambda: {"approved": True})


class FakeExecEngine:
    """
    Observable engine the tests assert against.
    app.run may call:
      - submit_market_order(...)
      - submit_limit_order(...)
      - submit_stop_order(...)
      - wait_for_order(...)
      - get_fill_details(...)
      - is_order_stale(...)
      - cancel_order(...)
    """
    def __init__(self):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._force_status = None
        self._stale = True

    def submit_market_order(self, **kwargs) -> str:
        self.calls.append(("submit_market_order", dict(kwargs)))
        return f"mkt-{kwargs.get('internal_order_id','x')}"

    def submit_limit_order(self, **kwargs) -> str:
        self.calls.append(("submit_limit_order", dict(kwargs)))
        return f"lmt-{kwargs.get('internal_order_id','x')}"

    def submit_stop_order(self, **kwargs) -> str:
        self.calls.append(("submit_stop_order", dict(kwargs)))
        return f"stp-{kwargs.get('internal_order_id','x')}"

    def wait_for_order(self, internal_order_id, broker_order_id, timeout_seconds, poll_interval):
        self.calls.append(("wait_for_order", {
            "internal_order_id": internal_order_id,
            "broker_order_id": broker_order_id,
            "timeout_seconds": timeout_seconds,
            "poll_interval": poll_interval,
        }))
        if self._force_status is not None:
            return self._force_status
        from core.state import OrderStatus
        return OrderStatus.FILLED

    def get_fill_details(self, internal_order_id: str):
        self.calls.append(("get_fill_details", {"internal_order_id": internal_order_id}))
        return (Decimal("1"), Decimal("100.00"))

    def is_order_stale(self, internal_order_id: str, ttl_seconds: int) -> bool:
        self.calls.append(("is_order_stale", {"internal_order_id": internal_order_id, "ttl_seconds": ttl_seconds}))
        return bool(self._stale)

    def cancel_order(self, internal_order_id: str, broker_order_id: str, reason: str):
        self.calls.append(("cancel_order", {
            "internal_order_id": internal_order_id,
            "broker_order_id": broker_order_id,
            "reason": reason
        }))
        return True


@dataclass
class FakeConfig:
    symbols: List[str]
    timeframe: str = "1Min"

    def __post_init__(self):
        self.broker = SimpleNamespace(api_key="DUMMY", api_secret="DUMMY", paper_trading=True)
        self.strategies = SimpleNamespace(
            enabled=[{
                "name": "VWAPMicroMeanReversion",
                "enabled": True,
                "config": {},
                "symbols": self.symbols,
                "timeframe": self.timeframe,
            }]
        )


class FakeContainer:
    """Must satisfy core.runtime.app.run() usage."""
    def __init__(self, cfg: FakeConfig, lifecycle: FakeLifecycle, exec_engine: FakeExecEngine):
        self._cfg = cfg
        self._lifecycle = lifecycle
        self._exec = exec_engine
        self._broker = None
        self._registry = FakeStrategyRegistry()
        self._positions = FakePositionStore()
        self._risk = FakeRiskManager()
        self._protections = FakeProtections()
        self._validator = FakeDataValidator()
        self._tracker = FakeOrderTracker()

    def initialize(self, _config_path) -> None:
        return

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def get_config(self) -> FakeConfig:
        return self._cfg

    def set_broker_connector(self, broker: Any) -> None:
        self._broker = broker

    def get_strategy_registry(self) -> FakeStrategyRegistry:
        return self._registry

    def get_strategy_lifecycle(self) -> FakeLifecycle:
        return self._lifecycle

    def get_order_execution_engine(self) -> FakeExecEngine:
        return self._exec

    def get_position_store(self) -> FakePositionStore:
        return self._positions

    def get_risk_manager(self) -> FakeRiskManager:
        return self._risk

    def get_protections(self) -> FakeProtections:
        return self._protections

    def get_data_validator(self) -> FakeDataValidator:
        return self._validator

    def get_order_tracker(self) -> FakeOrderTracker:
        return self._tracker

    def get_reconciler(self):
        return None


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path):
    """
    Runs core.runtime.app.run() exactly once with fakes.
    Returns (container, exec_engine).

    Supports:
      - force_status: override wait_for_order() return (e.g., OrderStatus.CANCELLED)
      - stale: override is_order_stale() result (default True)
    """
    import core.runtime.app as app_mod

    def _run_once(signals: List[Dict[str, Any]], *, force_status=None, stale: bool = True):
        monkeypatch.setenv("SIGNAL_COOLDOWN_SECONDS", "0")

        cfg = FakeConfig(symbols=["SPY"], timeframe="1Min")
        lifecycle = FakeLifecycle(signals=signals)
        exec_engine = FakeExecEngine()
        exec_engine._force_status = force_status
        exec_engine._stale = stale

        container = FakeContainer(cfg=cfg, lifecycle=lifecycle, exec_engine=exec_engine)

        monkeypatch.setattr(app_mod, "_ensure_strategy_registry_bootstrapped", lambda _c: None)
        monkeypatch.setattr(app_mod, "Container", lambda: container)
        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", lambda **kwargs: FakeBrokerConnector(**kwargs))

        opts = app_mod.RunOptions(
            config_path=tmp_path / "config.yaml",
            mode="paper",
            run_interval_s=60,
            run_once=True,
        )

        import yaml
        (tmp_path / "config.yaml").write_text(yaml.dump({"broker": {"api_key": "x", "api_secret": "y"}}))

        rc = app_mod.run(opts)
        assert rc == 0, f"app.run returned non-zero exit code: {rc}"

        return container, exec_engine

    return _run_once
