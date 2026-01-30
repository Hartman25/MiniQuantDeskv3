# tests/patch4/conftest.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest


@dataclass
class FakeRiskResult:
    approved: bool = True
    reason: str = "ok"
    approved_qty: Optional[Decimal] = None


class FakeRiskManager:
    def __init__(self):
        self.next_result = FakeRiskResult(approved=True)

    def validate_trade(self, **kwargs) -> FakeRiskResult:
        return self.next_result


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
    volume: int
    provider: str = "alpaca"

    def is_complete(self, timeframe: str) -> bool:
        return True


class FakeLifecycle:
    def __init__(self, signals: List[Dict[str, Any]]):
        self._signals = signals
        self.fills: List[Dict[str, Any]] = []

    def on_bar(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return list(self._signals)

    def on_order_filled(self, **kwargs) -> None:
        self.fills.append(kwargs)

    def add_strategy(self, s): ...
    def start_strategy(self, name: str): ...


class FakeStrategyRegistry:
    def register(self, cls): ...

    def create(self, **kwargs):
        class S:
            def __init__(self, symbols):
                self.symbols = symbols
                self.name = kwargs.get("name", "FAKE")

        return S(kwargs.get("symbols") or ["SPY"])


class FakeProtections:
    class Result:
        def __init__(self, is_protected: bool = False, reason: str = ""):
            self.is_protected = is_protected
            self.reason = reason

    def __init__(self):
        self.is_protected = False
        self.reason = ""

    def check(self, **kwargs):
        return FakeProtections.Result(self.is_protected, self.reason)


class FakeContainer:
    def __init__(self, lifecycle: FakeLifecycle, exec_engine: "FakeExecEngine", broker: "FakeBroker"):
        self._cfg = FakeConfig()
        self._lifecycle = lifecycle
        self._exec_engine = exec_engine
        self._broker = broker
        self._risk = FakeRiskManager()
        self._validator = FakeDataValidator()
        self._registry = FakeStrategyRegistry()
        self._protections = FakeProtections()

    def initialize(self, _path): ...
    def get_config(self): return self._cfg
    def set_broker_connector(self, broker): self._broker = broker
    def start(self): ...
    def stop(self): ...

    def get_protections(self): return self._protections
    def get_strategy_registry(self): return self._registry
    def get_strategy_lifecycle(self): return self._lifecycle
    def get_order_execution_engine(self): return self._exec_engine
    def get_risk_manager(self): return self._risk
    def get_data_validator(self): return self._validator


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
    strategies = [
        FakeStrategyCfg(
            name="VWAPMicroMeanReversion",
            enabled=True,
            symbols=["SPY"],
            timeframe="1Min",
            config={},
        )
    ]


class FakeBroker:
    def get_bars(self, symbol: str, timeframe: str, limit: int):
        # Enough bars so runtime doesn't early-exit
        n = max(60, int(limit) if limit else 60)
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        times = [end - timedelta(minutes=(n - 1 - i)) for i in range(n)]
        rows = []
        price = 100.0
        for i, _ts in enumerate(times):
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

    def get_position(self, symbol: str):
        return None

    def get_order_status(self, broker_order_id: str):
        return "filled", {"filled_qty": Decimal("1"), "filled_avg_price": Decimal("100")}

    def get_orders(self):
        return []
    
    def get_account_info(self) -> Dict[str, str]:
        return {"portfolio_value": "10000", "buying_power": "10000"}


class FakeExecEngine:
    def __init__(self):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.force_status = None

    def submit_limit_order(self, **kwargs) -> str:
        self.calls.append(("submit_limit_order", kwargs))
        return f"lmt-{kwargs['internal_order_id']}"

    def submit_market_order(self, **kwargs) -> str:
        self.calls.append(("submit_market_order", kwargs))
        return f"mkt-{kwargs['internal_order_id']}"

    def submit_stop_order(self, **kwargs) -> str:
        self.calls.append(("submit_stop_order", kwargs))
        return f"stp-{kwargs['internal_order_id']}"

    def cancel_order(self, **kwargs) -> None:
        self.calls.append(("cancel_order", kwargs))

    def wait_for_order(self, **kwargs):
        self.calls.append(("wait_for_order", kwargs))
        from core.state import OrderStatus
        if self.force_status is not None:
            return self.force_status
        return OrderStatus.FILLED

    def get_fill_details(self, internal_order_id: str):
        self.calls.append(("get_fill_details", {"internal_order_id": internal_order_id}))
        return Decimal("1"), Decimal("100")

    # Patch 4 helper might be called; make it configurable
    def is_order_stale(self, internal_order_id: str, ttl_seconds: int) -> bool:
        return True


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path):
    import core.runtime.app as app_mod

    def _fake_df_to_contracts(symbol, df):
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

    def _run_once(signals, *, force_status=None, stale=True):
        lifecycle = FakeLifecycle(signals=signals)
        exec_engine = FakeExecEngine()
        exec_engine.force_status = force_status
        exec_engine.is_order_stale = (lambda _id, _ttl: stale)

        broker = FakeBroker()
        container = FakeContainer(lifecycle, exec_engine, broker)

        # prevent real broker creation
        monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", lambda **_kwargs: broker)

        # use our container
        monkeypatch.setattr(app_mod, "Container", lambda: container)

        # no real strategy registry bootstrapping
        monkeypatch.setattr(app_mod, "_ensure_strategy_registry_bootstrapped", lambda _c: None)

        # ensure df->bars conversion works
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        # stop after one loop
        monkeypatch.setattr(app_mod.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(SystemExit()))

        opts = app_mod.RunOptions(config_path=tmp_path / "config.yaml", mode="paper", run_interval_s=60)
        with pytest.raises(SystemExit):
            app_mod.run(opts)

        return container, exec_engine

    return _run_once
