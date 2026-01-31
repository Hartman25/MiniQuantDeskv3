# tests/patch3/conftest.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest


class DummyLogger:
    def info(self, *args, **kwargs): ...
    def warning(self, *args, **kwargs): ...
    def error(self, *args, **kwargs): ...
    def exception(self, *args, **kwargs): ...


@dataclass
class FakeRiskResult:
    approved: bool = True
    reason: str = "ok"
    # patch3 might use any of these names:
    approved_qty: Optional[Decimal] = None
    approved_quantity: Optional[Decimal] = None
    capped_qty: Optional[Decimal] = None
    capped_quantity: Optional[Decimal] = None
    sized_qty: Optional[Decimal] = None
    sized_quantity: Optional[Decimal] = None


class FakeRiskManager:
    """
    Configurable risk behavior for tests.
    """
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

    # runtime may call on_bar(bar) or on_bar(strategy=..., bar=...)
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


class FakePositionStore:
    def __init__(self):
        self.positions: Dict[str, Any] = {}

    def upsert(self, pos: Any) -> None:
        self.positions[pos.symbol] = pos

    def delete(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    def get(self, symbol: str) -> Optional[Any]:
        return self.positions.get(symbol)


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
        self._pos_store = FakePositionStore()
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
    def get_position_store(self): return self._pos_store


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
    def __init__(self):
        self.open_orders = []
        self.position_qty: Dict[str, Decimal] = {}

    def set_position(self, symbol: str, qty: Decimal):
        self.position_qty[symbol] = qty

    def get_position(self, symbol: str):
        # return an object with .qty (string or Decimal ok)
        qty = self.position_qty.get(symbol, Decimal("0"))
        if qty == 0:
            return None

        class P:
            def __init__(self, sym, q):
                self.symbol = sym
                self.qty = str(q)

        return P(symbol, qty)

    def get_account_info(self) -> Dict[str, str]:
        return {"portfolio_value": "10000", "buying_power": "10000"}

    def get_bars(self, symbol: str, timeframe: str, limit: int):
        # Return enough bars so runtime doesn't early-continue
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

    def get_order_status(self, broker_order_id: str):
        # return tuple(status, details)
        return "filled", {"filled_qty": Decimal("1"), "filled_avg_price": Decimal("100")}

    def get_orders(self):
        return []


class FakeExecEngine:
    """
    Records call order so tests can assert behavior.
    """
    def __init__(self, buy_fill_qty=Decimal("1"), buy_fill_price=Decimal("100")):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._fill_qty = buy_fill_qty
        self._fill_price = buy_fill_price
        self._fill_map: Dict[str, Tuple[Decimal, Decimal]] = {}
        self._stop_ids: Dict[str, str] = {}
        self.force_status = None

    def submit_market_order(self, **kwargs) -> str:
        self.calls.append(("submit_market_order", kwargs))
        return f"mkt-{kwargs['internal_order_id']}"

    def submit_limit_order(self, **kwargs) -> str:
        self.calls.append(("submit_limit_order", kwargs))
        return f"lmt-{kwargs['internal_order_id']}"

    def submit_stop_order(self, **kwargs) -> str:
        self.calls.append(("submit_stop_order", kwargs))
        stop_id = f"stp-{kwargs['internal_order_id']}"
        self._stop_ids[kwargs["symbol"]] = stop_id
        return stop_id

    def cancel_order(self, **kwargs) -> None:
        self.calls.append(("cancel_order", kwargs))

    def wait_for_order(self, **kwargs):
        self.calls.append(("wait_for_order", kwargs))
        from core.state import OrderStatus
        if self.force_status is not None:
            return self.force_status
        internal_id = kwargs["internal_order_id"]
        self._fill_map[internal_id] = (self._fill_qty, self._fill_price)
        return OrderStatus.FILLED

    def get_fill_details(self, internal_order_id: str):
        self.calls.append(("get_fill_details", {"internal_order_id": internal_order_id}))
        if internal_order_id in self._fill_map:
            q, p = self._fill_map[internal_order_id]
            return q, p
        return None, None


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path):
    """
    Runs app.run() for one loop with controlled container/broker/engine/risk.
    Returns (container, exec_engine) so tests can set risk/position inputs.
    """
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

    def _run_once(signals, *, risk_result: Optional[FakeRiskResult] = None, position_qty: Optional[Decimal] = None,
                 buy_fill_qty=Decimal("1"), buy_fill_price=Decimal("100"), force_status=None):
        lifecycle = FakeLifecycle(signals=signals)
        exec_engine = FakeExecEngine(buy_fill_qty=buy_fill_qty, buy_fill_price=buy_fill_price)
        exec_engine.force_status = force_status
        broker = FakeBroker()
        if position_qty is not None:
            broker.set_position("SPY", position_qty)

        container = FakeContainer(lifecycle, exec_engine, broker)
        if risk_result is not None:
            container._risk.next_result = risk_result

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

        # run
        opts = app_mod.RunOptions(config_path=tmp_path / "config.yaml", mode="paper", run_interval_s=60)
        with pytest.raises(SystemExit):
            app_mod.run(opts)

        return container, exec_engine

    return _run_once
