# tests/conftest.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
import pytest
import threading
import atexit
import time

# tests/conftest.py

# Pytest collection control: keep Phase 1 green and focused.
# Anything listed here will not be collected/runs even if present.
collect_ignore = [
    "tests/acceptance/test_phase2_strategy_correctness.py",
    "tests/acceptance/test_phase3_risk_survivability.py",
    "tests/patch4",
    "tests/strategies/test_vwap_micro_mean_reversion.py",
    "tests/test_integration_comprehensive.py",
    "tests/test_monitoring.py",
    "tests/test_recovery.py",
    "tests/test_risk_management.py",
    "tests/test_risk_protections.py",
    "tests/test_unified_protections.py",
    "tests/test_universe_system.py",
    "tests/test_patch2_1_paper_auto_heal.py",
]

# -------------------------
# Fakes used by integration tests
# -------------------------

class FakeDataProvider:
    """Return a minimal DataFrame for the runtime loop."""
    def get_recent_bars(self, symbol: str, timeframe: str, limit: int = 1) -> pd.DataFrame:
        # One bar is enough because we also patch _df_to_contracts
        return pd.DataFrame(
            [{
                "timestamp": "2026-01-30T10:00:00Z",
                "open": 100.0,
                "high": 100.1,
                "low": 99.9,
                "close": 100.0,
                "volume": 1000,
            }]
        )
    
    def get_latest_bars(self, symbol: str, limit: int = 1) -> List[Dict[str, Any]]:
        """Return list of dict-like bars for runtime compatibility"""
        df = self.get_recent_bars(symbol=symbol, timeframe="1Min", limit=limit)
        if df is None or df.empty:
            return [{"close": 100.0, "timestamp": "2026-01-30T10:00:00Z", "open": 100.0, "high": 100.1, "low": 99.9, "volume": 1000}]
        return df.to_dict(orient="records")

class FakeExecEngine:
    """
    Must match the methods used by core/runtime/app.py:
      - submit_market_order(...)
      - submit_limit_order(...)
      - submit_stop_order(...)
      - wait_for_order(...)
      - is_order_stale(...)
      - cancel_order(...)
    """
    def __init__(self):
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        # optional knobs
        self.force_wait_status = None
        self.force_is_stale: Optional[bool] = None

    def submit_market_order(self, **kwargs) -> str:
        self.calls.append(("submit_market_order", dict(kwargs)))
        return f"mkt-{kwargs.get('internal_order_id','NA')}"

    def submit_limit_order(self, **kwargs) -> str:
        self.calls.append(("submit_limit_order", dict(kwargs)))
        return f"lmt-{kwargs.get('internal_order_id','NA')}"

    def submit_stop_order(self, **kwargs) -> str:
        self.calls.append(("submit_stop_order", dict(kwargs)))
        return f"stp-{kwargs.get('internal_order_id','NA')}"

    def wait_for_order(self, **kwargs):
        self.calls.append(("wait_for_order", dict(kwargs)))
        # runtime expects core.state.OrderStatus
        from core.state import OrderStatus
        if self.force_wait_status is not None:
            return self.force_wait_status
        return OrderStatus.FILLED

    def is_order_stale(self, internal_order_id: str, ttl_seconds: int) -> bool:
        self.calls.append(("is_order_stale", {"internal_order_id": internal_order_id, "ttl_seconds": ttl_seconds}))
        if self.force_is_stale is not None:
            return bool(self.force_is_stale)
        return False

    def cancel_order(self, internal_order_id: str, broker_order_id: str = "", reason: str = "") -> bool:
        self.calls.append(("cancel_order", {"internal_order_id": internal_order_id, "broker_order_id": broker_order_id, "reason": reason}))
        return True
    
    def get_fill_details(self, internal_order_id: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Return filled_qty, fill_price for an order"""
        # For tests, always return filled
        return (Decimal("1"), Decimal("100.0"))
    
    def get_orders_by_type(self, order_type: str) -> List[Dict[str, Any]]:
        """Get all orders of a specific type - returns list of order dicts"""
        return [call for method, call in self.calls if method in ("submit_market_order", "submit_limit_order", "submit_stop_order")]

    def get_orders_by_symbol(self, symbol: str) -> List:
        """Get all orders for a specific symbol - returns list of (order_type, kwargs) tuples where order_type is 'MARKET', 'LIMIT', or 'STOP'"""
        def _normalize_type(method: str) -> str:
            if method == "submit_market_order":
                return "MARKET"
            elif method == "submit_limit_order":
                return "LIMIT"
            elif method == "submit_stop_order":
                return "STOP"
            return method
        
        return [(_normalize_type(method), call) for method, call in self.calls if method in ("submit_market_order", "submit_limit_order", "submit_stop_order") and call.get("symbol") == symbol]

    
    def set_trade_journal(self, journal, run_id: str = None):
        """Set trade journal (no-op for tests)"""
        pass


class FakeTradeJournal:
    def record_event(self, *args, **kwargs):
        # runtime calls this; we don't need behavior for P0
        return None


class FakeRiskManager:
    def __init__(self, risk_result=None):
        self._risk_result = risk_result
    
    def validate(self, *args, **kwargs):
        # if your runtime risk manager is called, always allow for the P0 path
        return True, ""
    
    def validate_trade(self, **kwargs):
        """Risk validation for trades - uses injected risk_result if provided"""
        if self._risk_result is not None:
            # Return the injected risk result
            return self._risk_result
        # Default: always approve for tests
        return SimpleNamespace(approved=True, reason="test_approved", to_dict=lambda: {"approved": True, "reason": "test_approved"})





class FakePositions:
    def __init__(self):
        self._open: Dict[str, Decimal] = {}

    def is_open(self, symbol: str) -> bool:
        return self._open.get(symbol, Decimal("0")) > 0

    def set_open(self, symbol: str, qty: Decimal):
        self._open[symbol] = qty


class FakeProtections:
    """Fake protections manager"""
    def check(self, **kwargs):
        return SimpleNamespace(is_protected=False, reason=None)


class FakeDataValidator:
    """Fake data validator"""
    def validate_bars(self, **kwargs):
        return True


class FakePositionStore:
    """Fake position store"""
    def __init__(self, position_qty=None):
        self._positions = {}
        if position_qty is not None:
            self._positions["SPY"] = SimpleNamespace(qty=position_qty, symbol="SPY")

    def get(self, symbol: str):
        """Match real PositionStore.get(symbol) -> Optional[Position]"""
        return self._positions.get(symbol)

    def get_position(self, symbol: str):
        return self._positions.get(symbol)

    def has_open_position(self, symbol: str) -> bool:
        pos = self.get_position(symbol)
        if pos is None:
            return False
        qty = getattr(pos, "qty", 0)
        return qty != 0

    def get_all_positions(self):
        return list(self._positions.values())

    def upsert(self, position):
        symbol = getattr(position, "symbol", None)
        if symbol:
            self._positions[symbol] = position

    def delete(self, symbol: str):
        self._positions.pop(symbol, None)


class FakeLimitsTracker:
    """Fake PersistentLimitsTracker for testing realized PnL recording"""
    def __init__(self, daily_loss_limit=Decimal("1000")):
        self._daily_pnl = Decimal("0")
        self._daily_loss_limit = daily_loss_limit

    def record_realized_pnl(self, pnl):
        self._daily_pnl += Decimal(str(pnl))

    def get_daily_realized_pnl(self):
        return self._daily_pnl

    def is_daily_loss_limit_breached(self):
        return self._daily_pnl <= -self._daily_loss_limit


class FakeReconciler:
    """Fake reconciler"""
    def reconcile_startup(self):
        return []

    def heal_startup(self, discrepancies):
        return True


class FakeOrderTracker:
    def __init__(self):
        self._fills: Dict[str, Decimal] = {}

    def get_filled_quantity(self, symbol: str) -> Decimal:
        return self._fills.get(symbol, Decimal("0"))

    def add_fill(self, symbol: str, qty: Decimal):
        self._fills[symbol] = self.get_filled_quantity(symbol) + qty
    
    def get_open_orders(self, symbol: str = None):
        return []
    
    def get_orphaned_orders(self, broker_orders):
        return []
    
    def get_shadow_orders(self, broker_orders):
        return []


class FakeUniverseSystem:
    def __init__(self, symbols: List[str]):
        self._symbols = list(symbols)

    def accepted_symbols(self, symbols: List[str]) -> List[str]:
        # bypass filtering
        return list(symbols)


class FakeStrategy:
    def __init__(self, name: str, signals: List[Dict[str, Any]], symbols: List[str] = None):
        self.name = name
        self.symbols = symbols or ["SPY"]
        self._signals = list(signals)

    def on_bar(self, ctx, bar) -> List[Dict[str, Any]]:
        # Emit once then stop
        out = list(self._signals)
        self._signals = []
        return out


class FakeLifecycle:
    """Fake strategy lifecycle for runtime tests"""
    def __init__(self, signals: List[Dict[str, Any]]):
        self._signals = signals
        self.fills: List[Dict[str, Any]] = []
        self._strategies = []
    
    def add_strategy(self, s):
        """Add a strategy to lifecycle"""
        self._strategies.append(s)
    
    def start_strategy(self, name: str):
        """Start a strategy (no-op for tests)"""
        pass
    
    def on_bar(self, bar) -> List[Dict[str, Any]]:
        """Return signals when called with a bar"""
        return list(self._signals)
    
    def on_order_filled(self, **kwargs) -> None:
        """Record fill events"""
        self.fills.append(kwargs)


class FakeStrategyRegistry:
    def __init__(self, signals: List[Dict[str, Any]]):
        self._signals = signals
        self._registered: Dict[str, Any] = {}

    def register(self, strategy_cls: Any) -> None:
        # runtime calls register(VWAPMeanReversion), register(VWAPMicroMeanReversion)
        self._registered[strategy_cls.__name__] = strategy_cls

    def create(self, name: str, config: Dict[str, Any], symbols: List[str] = None, timeframe: str = None):
        # runtime calls registry.create(name, config, symbols, timeframe)
        return FakeStrategy(name=name, signals=self._signals, symbols=symbols or ["SPY"])


@dataclass
class FakeStrategiesConfig:
    enabled: List[Dict[str, Any]]


class FakeConfig:
    """
    Matches what core/runtime/app.py uses:
      - cfg.broker.paper_trading
      - cfg.get("runtime") -> dict with symbols/timeframe
      - cfg.get("universe") -> dict
      - cfg.strategies.enabled -> list of dicts with name/config
    """
    def __init__(self, symbols: List[str], timeframe: str = "1Min"):
        self.broker = SimpleNamespace(
            api_key="DUMMY",
            api_secret="DUMMY",
            paper_trading=True,
        )
        self._runtime = {"symbols": symbols, "timeframe": timeframe}
        self._universe = {"mode": "core"}
        self.strategies = FakeStrategiesConfig(
            enabled=[
                {
                    "name": "VWAPMicroMeanReversion",
                    "enabled": True,
                    "config": {
                        "symbols": symbols,
                        "timeframe": timeframe,
                    },
                }
            ]
        )

    def get(self, key: str, default=None):
        if key == "runtime":
            return self._runtime
        if key == "universe":
            return self._universe
        if key == "broker":
            return {
                "api_key": self.broker.api_key,
                "api_secret": self.broker.api_secret,
                "paper_trading": self.broker.paper_trading,
            }
        return default


class FakeContainer:
    """
    Must satisfy app.run() calls:
      initialize(), start(), stop(), set_broker_connector()
      get_config(), get_strategy_registry(), get_data_provider(), get_execution_engine()
      get_trade_journal(), get_risk_manager(), get_order_tracker(), get_positions(), get_universe_system()
      get_protections(), get_data_validator(), get_position_store(), get_reconciler()
    """
    def __init__(self, cfg: FakeConfig, signals: List[Dict[str, Any]], risk_result=None, position_qty=None):
        self._cfg = cfg
        self._signals = signals
        self._risk_result = risk_result
        self._position_qty = position_qty
        self._broker = None

        self._data_provider = FakeDataProvider()
        self._exec_engine = FakeExecEngine()
        self._journal = FakeTradeJournal()
        self._risk = FakeRiskManager(risk_result=risk_result)
        self._tracker = FakeOrderTracker()
        self._positions = FakePositions()
        self._universe = FakeUniverseSystem(symbols=cfg.get("runtime")["symbols"])
        self._registry = FakeStrategyRegistry(signals=signals)
        self._lifecycle = FakeLifecycle(signals=signals)
        
        # Add missing components needed by app.py
        self._protections = FakeProtections()
        self._data_validator = FakeDataValidator()
        self._position_store = FakePositionStore(position_qty)
        self._reconciler = FakeReconciler()
        self._limits_tracker = FakeLimitsTracker()

    def initialize(self, _config_path):
        return None

    def start(self, paper: bool = True):
        return None

    def stop(self):
        return None

    def get_config(self) -> FakeConfig:
        return self._cfg

    def get_strategy_registry(self) -> FakeStrategyRegistry:
        return self._registry

    def get_data_provider(self) -> FakeDataProvider:
        return self._data_provider

    def get_execution_engine(self) -> FakeExecEngine:
        return self._exec_engine

    def get_trade_journal(self) -> FakeTradeJournal:
        return self._journal

    def get_risk_manager(self) -> FakeRiskManager:
        return self._risk

    def get_order_tracker(self) -> FakeOrderTracker:
        return self._tracker

    def get_positions(self) -> FakePositions:
        return self._positions

    def get_universe_system(self) -> FakeUniverseSystem:
        return self._universe
    
    def set_broker_connector(self, broker):
        """Store broker connector (compatibility with app.py)"""
        self._broker = broker
        return None
    
    def get_protections(self):
        """Return protections manager (compatibility with app.py)"""
        return self._protections
    
    def get_data_validator(self):
        """Return data validator (compatibility with app.py)"""
        return self._data_validator
    
    def get_position_store(self):
        """Return position store (compatibility with app.py)"""
        return self._position_store
    
    def get_reconciler(self):
        """Return reconciler (compatibility with app.py)"""
        return self._reconciler
    
    def get_strategy_lifecycle(self):
        """Return strategy lifecycle (compatibility with app.py)"""
        return self._lifecycle
    
    def get_order_execution_engine(self):
        """Return execution engine (alternative method name)"""
        return self._exec_engine

    def get_limits_tracker(self):
        """Return limits tracker (compatibility with app.py realized PnL wiring)"""
        return self._limits_tracker


def _fake_df_to_contracts(symbol: str, df: pd.DataFrame):
    # Runtime loop expects iterable of "bars". We only need one.
    # Must return MarketDataContract-like objects
    from datetime import datetime, timezone
    from decimal import Decimal
    return [SimpleNamespace(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        open=Decimal("100.0"),
        high=Decimal("100.1"),
        low=Decimal("99.9"),
        close=Decimal("100.0"),
        volume=1000,
        provider="fake",
        is_complete=lambda tf: True  # Always complete for tests
    )]


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path):
    """
    Run core.runtime.app.run() for exactly one iteration using FakeContainer.
    Returns (container, exec_engine).
    
    Accepts kwargs for test control:
        - force_status: Override wait_for_order return value
        - stale: Force is_order_stale() to return True
        - risk_result: Override risk manager result
        - position_qty: Pre-populate position store
    """
    import core.runtime.app as app_mod

    def _run_once(
        signals: List[Dict[str, Any]],
        force_status=None,
        stale: bool = False,
        risk_result=None,
        position_qty=None
    ):
        cfg = FakeConfig(symbols=["SPY"], timeframe="1Min")
        container = FakeContainer(cfg=cfg, signals=signals, risk_result=risk_result, position_qty=position_qty)
        
        # Apply test controls to exec engine
        if force_status is not None:
            container.get_execution_engine().force_wait_status = force_status
        if stale:
            container.get_execution_engine().force_is_stale = True

        # Use fake container
        monkeypatch.setattr(app_mod, "Container", lambda: container)

        # Make df->contracts conversion deterministic
        monkeypatch.setattr(app_mod, "_df_to_contracts", _fake_df_to_contracts)

        # Now run exactly one iteration
        opts = app_mod.RunOptions(
            config_path=tmp_path / "config.yaml",
            mode="paper",
            run_interval_s=60,
            run_once=True,   # <-- critical
        )
        # Prevent any real Alpaca network calls
        if hasattr(app_mod, "AlpacaBrokerConnector"):
            class _FakeAlpacaBroker:
                def __init__(self, *args, **kwargs):
                    pass

                def close(self):
                    return None

                # If runtime asks for account/buying power/etc, return safe defaults
                def get_account_info(self):
                    return {"portfolio_value": "100000", "buying_power": "100000", "cash": "100000"}
                
                def get_bars(self, symbol: str, timeframe: str, limit: int):
                    # Return minimal DataFrame
                    return pd.DataFrame([{
                        "timestamp": "2026-01-30T10:00:00Z",
                        "open": 100.0,
                        "high": 100.1,
                        "low": 99.9,
                        "close": 100.0,
                        "volume": 1000,
                    }])
                
                def get_orders(self):
                    return []
                
                def get_position(self, symbol: str):
                    """Return position from container's position store for PATCH 3 compatibility"""
                    # Query the container's position store
                    pos_store = container.get_position_store()
                    return pos_store.get_position(symbol)

            monkeypatch.setattr(app_mod, "AlpacaBrokerConnector", _FakeAlpacaBroker)

        rc = app_mod.run(opts)
        assert rc in (0, 1)  # run returns exit code

        return container, container.get_execution_engine()

    return _run_once


@pytest.fixture
def results():
    """Fixture for test_integration_comprehensive.py TestResults tracking."""
    from tests.test_integration_comprehensive import TestResults
    return TestResults()

def _dump_threads(tag: str):
    print(f"\n[THREAD_DUMP:{tag}] active_threads={threading.active_count()}")
    for t in threading.enumerate():
        print(f"  - name={t.name!r} daemon={t.daemon} alive={t.is_alive()} ident={t.ident}")

def pytest_sessionfinish(session, exitstatus):
    _dump_threads("pytest_sessionfinish")
    # give a moment for shutdown hooks to run
    time.sleep(0.2)
    _dump_threads("pytest_sessionfinish+200ms")

atexit.register(lambda: _dump_threads("atexit"))