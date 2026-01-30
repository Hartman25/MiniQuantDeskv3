# tests/patch2/conftest.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest


class DummyLogger:
    def info(self, *args, **kwargs): ...
    def warning(self, *args, **kwargs): ...
    def error(self, *args, **kwargs): ...
    def exception(self, *args, **kwargs): ...


@dataclass
class FakeProtResult:
    is_protected: bool = False
    reason: str = ""
    until: Optional[datetime] = None


class FakeProtections:
    def check(self, **kwargs) -> FakeProtResult:
        return FakeProtResult(is_protected=False)


@dataclass
class FakeRiskResult:
    approved: bool = True
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"approved": self.approved, "reason": self.reason}


class FakeRiskManager:
    def validate_trade(self, **kwargs) -> FakeRiskResult:
        return FakeRiskResult(approved=True)


class FakeDataValidator:
    def validate_bars(self, **kwargs) -> None:
        return


@dataclass
class FakeBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0
    provider: str = "alpaca"

    def is_complete(self, timeframe: str) -> bool:
        return True


class FakeLifecycle:
    def __init__(self, signals: List[Dict[str, Any]]):
        self._signals = signals
        self.fills: List[Dict[str, Any]] = []

    def on_bar(self, *args, **kwargs) -> List[Dict[str, Any]]:
        # app.py may call on_bar(bar) OR on_bar(strategy_name=..., bar=...)
        return list(self._signals)

    def on_order_filled(self, *args, **kwargs) -> None:
        # accept both positional or kw-style
        self.fills.append({"args": args, "kwargs": kwargs})

    def add_strategy(self, *args, **kwargs): ...
    def start_strategy(self, *args, **kwargs): ...


class FakeStrategyRegistry:
    def register(self, cls): ...
    def create(self, **kwargs):
        # minimal object with .symbols and .name
        class S:
            def __init__(self, symbols): self.symbols = symbols; self.name = kwargs.get("name", "FAKE")
        return S(kwargs.get("symbols") or ["SPY"])


class FakePositionStore:
    def __init__(self):
        self.positions: Dict[str, Any] = {}

    def upsert(self, pos: Any) -> None:
        self.positions[pos.symbol] = pos

    def delete(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    def get(self, symbol: str) -> Optional[Any]:
        return self.positions.get(symbol)


class FakeOrderTracker:
    def start_tracking(self, order): ...
    def stop_tracking(self, *args, **kwargs): ...
    def get_orphaned_orders(self, broker_orders): return []
    def get_shadow_orders(self, broker_orders): return []


class FakeBroker:
    def __init__(self):
        self.open_orders = []
        self.last_submitted: List[Tuple[str, Dict[str, Any]]] = []

    def get_account_info(self) -> Dict[str, str]:
        return {"portfolio_value": "10000", "buying_power": "10000"}

    def get_bars(self, symbol: str, timeframe: str, limit: int):
        import pandas as pd
        from datetime import datetime, timezone, timedelta

        n = max(60, int(limit) if limit else 60)
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        times = [end - timedelta(minutes=(n - 1 - i)) for i in range(n)]

        rows = []
        price = 100.0
        for i, ts in enumerate(times):
            o = price
            h = price + 0.2
            l = price - 0.2
            c = price + (0.05 if i % 2 == 0 else -0.05)
            v = 1000 + i
            rows.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
            price = c

        df = pd.DataFrame(rows, index=[pd.Timestamp(t) for t in times])
        df.index.name = "timestamp"
        return df


    def get_order_status(self, broker_order_id: str):
        # return status, fill_info
        return "filled", {"filled_qty": Decimal("1"), "filled_avg_price": Decimal("100")}

    def get_orders(self):
        return []


class FakeExecEngine:
    """
    Records call order so tests can assert cancel-before-sell, etc.
    """
    def __init__(self, buy_fill_qty=Decimal("1"), buy_fill_price=Decimal("100")):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._fill_qty = buy_fill_qty
        self._fill_price = buy_fill_price
        self._fill_map: Dict[str, Tuple[Decimal, Decimal]] = {}
        self._stop_ids: Dict[str, str] = {}

    def submit_market_order(self, **kwargs) -> str:
        self.calls.append(("submit_market_order", kwargs))
        # broker order id
        return f"mkt-{kwargs['internal_order_id']}"

    def submit_limit_order(self, **kwargs) -> str:
        self.calls.append(("submit_limit_order", kwargs))
        return f"lmt-{kwargs['internal_order_id']}"

    def wait_for_order(self, **kwargs):
        self.calls.append(("wait_for_order", kwargs))

        from core.state import OrderStatus

        # If the test forces a status, honor it.
        # Only write fill details when it's actually filled.
        if getattr(self, "force_status", None) is not None:
            if self.force_status == OrderStatus.FILLED:
                internal_id = kwargs["internal_order_id"]
                self._fill_map[internal_id] = (self._fill_qty, self._fill_price)
            return self.force_status

        # Default: Simulate FILLED
        internal_id = kwargs["internal_order_id"]
        self._fill_map[internal_id] = (self._fill_qty, self._fill_price)
        return OrderStatus.FILLED

    def get_fill_details(self, internal_order_id: str):
        self.calls.append(("get_fill_details", {"internal_order_id": internal_order_id}))
        return self._fill_map.get(internal_order_id, (None, None))

    def cancel_order(self, **kwargs) -> bool:
        self.calls.append(("cancel_order", kwargs))
        return True

    def submit_stop_order(self, **kwargs) -> str:
        self.calls.append(("submit_stop_order", kwargs))
        stop_id = f"stp-{kwargs['internal_order_id']}"
        self._stop_ids[kwargs["symbol"]] = stop_id
        return stop_id


class FakeStrategyCfg:
    def __init__(self, name="VWAPMicroMeanReversion", enabled=True, symbols=None, timeframe="1Min", config=None):
        self.name = name
        self.enabled = enabled
        self.symbols = symbols or ["SPY"]
        self.timeframe = timeframe
        self.config = config or {}


class FakeConfig:
    class Broker:
        api_key = "x"
        api_secret = "y"
        paper_trading = True
    broker = Broker()

    # IMPORTANT: app.py expects strat_cfg.name / .config / .symbols / .timeframe
    strategies = [
        FakeStrategyCfg(
            name="VWAPMicroMeanReversion",
            enabled=True,
            symbols=["SPY"],
            timeframe="1Min",
            config={},
        )
    ]

class FakeContainer:
    def __init__(self, lifecycle: FakeLifecycle, exec_engine: FakeExecEngine, broker: FakeBroker):
        self._cfg = FakeConfig()
        self._lifecycle = lifecycle
        self._exec = exec_engine
        self._broker = broker
        self._risk = FakeRiskManager()
        self._validator = FakeDataValidator()
        self._protections = FakeProtections()
        self._registry = FakeStrategyRegistry()
        self._pos = FakePositionStore()
        self._tracker = FakeOrderTracker()

    def initialize(self, config_path: Path) -> None: ...
    def get_config(self): return self._cfg
    def set_broker_connector(self, broker): self._broker = broker
    def start(self): ...
    def get_protections(self): return self._protections
    def get_strategy_registry(self): return self._registry
    def get_strategy_lifecycle(self): return self._lifecycle
    def get_order_execution_engine(self): return self._exec
    def get_risk_manager(self): return self._risk
    def get_data_validator(self): return self._validator
    def get_position_store(self): return self._pos
    def get_order_tracker(self): return self._tracker


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path):
    """
    Provides a helper to run core.runtime.app.run() once with fakes.
    """
    import core.runtime.app as app_mod

    def _run_once(signals, buy_fill_qty=Decimal("1"), buy_fill_price=Decimal("100"), force_status=None):
        lifecycle = FakeLifecycle(signals=signals)
        exec_engine = FakeExecEngine(buy_fill_qty=buy_fill_qty, buy_fill_price=buy_fill_price)
        exec_engine.force_status = force_status
        broker = FakeBroker()

        # Prevent app.run() from constructing a real AlpacaBrokerConnector (network call)
        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", lambda **_kwargs: broker)

        # Monkeypatch Container() constructed inside app.run()
        monkeypatch.setattr(app_mod, "Container", lambda: FakeContainer(lifecycle, exec_engine, broker))
        # app.py expects a DataFrame and converts it via _df_to_contracts.
        # We override it to return FakeBar objects consistently for the runtime/strategy.
        def _fake_df_to_contracts(symbol, df):
            from decimal import Decimal
            bars = []
            for ts, row in df.iterrows():
                bars.append(
                    FakeBar(
                        symbol=symbol,
                        timestamp=ts.to_pydatetime(),
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=int(row.get("volume", 0)),
                        provider="alpaca",
                    )
                )
            return bars

        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        monkeypatch.setattr(app_mod, "_ensure_strategy_registry_bootstrapped", lambda container: None)
        monkeypatch.setattr(app_mod.time, "sleep", lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit()))
        if hasattr(app_mod, "is_market_open"):
            monkeypatch.setattr(app_mod, "is_market_open", lambda *a, **k: True)
        for name in ("market_is_open", "is_trading_hours", "within_market_hours"):
            if hasattr(app_mod, name):
                monkeypatch.setattr(app_mod, name, lambda *a, **k: True)
        monkeypatch.setenv("TRADE_ONLY_RTH", "0")
        monkeypatch.setenv("ONLY_TRADE_MARKET_HOURS", "0")

        opts = app_mod.RunOptions(config_path=tmp_path / "config.yaml", mode="paper", run_interval_s=60)
        with pytest.raises(SystemExit):
            app_mod.run(opts)

        return lifecycle, exec_engine

    return _run_once
