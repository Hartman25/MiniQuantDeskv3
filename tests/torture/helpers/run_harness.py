"""
Controlled runtime harness for torture tests.

Drives the runtime loop from core.runtime.app for a fixed number of cycles
by monkeypatching:
  - Container → FakeContainer with injected broker
  - AlpacaBrokerConnector → intercepted so no real network calls
  - time.sleep → SleepRecorder (no real sleeping)
  - signal.signal → no-op (avoid interfering with pytest)

The harness writes journal events to a temp directory so tests can
inspect JSONL output.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

from tests.torture.helpers.fake_time import FakeClock, SleepRecorder


# ---------------------------------------------------------------------------
# Lightweight fakes (reuse patterns from tests/conftest.py but independent)
# ---------------------------------------------------------------------------

class _FakeProtections:
    def check(self, **kw):
        return SimpleNamespace(is_protected=False, reason=None, until=None)


class _FakeDataValidator:
    def validate_bars(self, **kw):
        return True


class _FakePositionStore:
    def __init__(self):
        self._positions: Dict[str, Any] = {}

    def get(self, symbol):
        return self._positions.get(symbol)

    def get_position(self, symbol):
        return self._positions.get(symbol)

    def has_open_position(self, symbol):
        pos = self.get_position(symbol)
        if pos is None:
            return False
        return float(getattr(pos, "qty", 0) or 0) != 0.0

    def get_all_positions(self):
        return list(self._positions.values())

    def upsert(self, position):
        symbol = getattr(position, "symbol", None)
        if symbol:
            self._positions[symbol] = position

    def delete(self, symbol):
        self._positions.pop(symbol, None)

    def close(self):
        pass


class _FakeReconciler:
    def reconcile_startup(self):
        return []

    def heal_startup(self, disc):
        return True


class _FakeOrderTracker:
    def get_open_orders(self, symbol=None):
        return []

    def get_open_orders_for_symbol(self, symbol):
        return []

    def get_orphaned_orders(self, broker_orders):
        return []

    def get_shadow_orders(self, broker_orders):
        return []


class _FakeLimitsTracker:
    def __init__(self):
        self._daily_pnl = Decimal("0")

    def record_realized_pnl(self, pnl):
        self._daily_pnl += Decimal(str(pnl))


class _FakeRiskManager:
    def validate_trade(self, **kw):
        return SimpleNamespace(
            approved=True,
            reason="test_approved",
            to_dict=lambda: {"approved": True, "reason": "test_approved"},
        )


class _FakeLifecycle:
    """Lifecycle that emits signals for N cycles then goes quiet."""

    def __init__(self, signals_per_cycle: Optional[List[List[Dict]]] = None):
        self._signals_per_cycle = signals_per_cycle or []
        self._cycle = 0
        self.fills: List[Dict] = []

    def add_strategy(self, s):
        pass

    def start_strategy(self, name):
        pass

    def on_bar(self, bar) -> List[Dict[str, Any]]:
        idx = self._cycle
        self._cycle += 1
        if idx < len(self._signals_per_cycle):
            return list(self._signals_per_cycle[idx])
        return []

    def on_order_filled(self, **kw):
        self.fills.append(kw)


class _FakeStrategyRegistry:
    def register(self, cls):
        pass

    def create(self, name, config, symbols=None, timeframe=None):
        return SimpleNamespace(name=name, symbols=symbols or ["SPY"])


class _FakeExecEngine:
    def __init__(self):
        self.calls: List[tuple] = []

    def submit_market_order(self, **kw) -> str:
        self.calls.append(("submit_market_order", dict(kw)))
        return f"harness-mkt-{kw.get('internal_order_id', 'NA')}"

    def submit_limit_order(self, **kw) -> str:
        self.calls.append(("submit_limit_order", dict(kw)))
        return f"harness-lmt-{kw.get('internal_order_id', 'NA')}"

    def submit_stop_order(self, **kw) -> str:
        self.calls.append(("submit_stop_order", dict(kw)))
        return f"harness-stp-{kw.get('internal_order_id', 'NA')}"

    def wait_for_order(self, **kw):
        self.calls.append(("wait_for_order", dict(kw)))
        from core.state import OrderStatus
        return OrderStatus.FILLED

    def get_fill_details(self, internal_order_id):
        return (Decimal("1"), Decimal("100.00"))

    def cancel_order(self, internal_order_id="", broker_order_id="", reason=""):
        self.calls.append(("cancel_order", {"internal_order_id": internal_order_id}))
        return True

    def register_trade_id(self, internal_order_id, trade_id):
        pass

    def set_trade_journal(self, journal, run_id=None):
        pass

    def get_open_orders(self, symbol=None):
        return []


class _FakeConfig:
    def __init__(self, symbols=None):
        self.broker = SimpleNamespace(
            api_key="FAKE", api_secret="FAKE", paper_trading=True
        )
        self.strategies = SimpleNamespace(
            enabled=[
                {
                    "name": "VWAPMicroMeanReversion",
                    "enabled": True,
                    "config": {},
                    "symbols": symbols or ["SPY"],
                    "timeframe": "1Min",
                }
            ]
        )


class HarnessContainer:
    """Minimal container that wires a custom broker + fakes.

    Constructed by the harness; injected into app.run() via monkeypatch.
    """

    def __init__(
        self,
        broker,
        symbols: List[str],
        lifecycle: Optional[_FakeLifecycle] = None,
        exec_engine: Optional[_FakeExecEngine] = None,
    ):
        self._broker = broker
        self._cfg = _FakeConfig(symbols=symbols)
        self._lifecycle = lifecycle or _FakeLifecycle()
        self._exec_engine = exec_engine or _FakeExecEngine()
        self._position_store = _FakePositionStore()
        self._reconciler = _FakeReconciler()
        self._order_tracker = _FakeOrderTracker()
        self._limits_tracker = _FakeLimitsTracker()
        self._protections = _FakeProtections()
        self._data_validator = _FakeDataValidator()
        self._risk_manager = _FakeRiskManager()
        self._registry = _FakeStrategyRegistry()

    def initialize(self, config_path):
        pass

    def start(self, **kw):
        pass

    def stop(self):
        pass

    def set_broker_connector(self, broker):
        self._broker = broker

    def get_config(self):
        return self._cfg

    def get_protections(self):
        return self._protections

    def get_data_validator(self):
        return self._data_validator

    def get_position_store(self):
        return self._position_store

    def get_reconciler(self):
        return self._reconciler

    def get_strategy_registry(self):
        return self._registry

    def get_strategy_lifecycle(self):
        return self._lifecycle

    def get_order_execution_engine(self):
        return self._exec_engine

    def get_risk_manager(self):
        return self._risk_manager

    def get_order_tracker(self):
        return self._order_tracker

    def get_limits_tracker(self):
        return self._limits_tracker


# ---------------------------------------------------------------------------
# Harness result
# ---------------------------------------------------------------------------

@dataclass
class HarnessResult:
    """Captures everything from a harness run for assertions."""

    exit_code: int
    sleep_recorder: SleepRecorder
    journal_dir: Path
    container: HarnessContainer
    broker: Any
    exec_engine: _FakeExecEngine
    lifecycle: _FakeLifecycle
    error: Optional[Exception] = None

    # -- convenience ---------------------------------------------------------

    def journal_events(self) -> List[Dict[str, Any]]:
        """Read all JSONL events written to the daily journal."""
        events: List[Dict[str, Any]] = []
        daily_dir = self.journal_dir / "daily"
        if not daily_dir.exists():
            return events
        for f in sorted(daily_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.journal_events() if e.get("event") == event_type]

    def trade_journal_events(self) -> List[Dict[str, Any]]:
        """Read all JSONL events written to the trades journal."""
        events: List[Dict[str, Any]] = []
        trades_dir = self.journal_dir / "trades"
        if not trades_dir.exists():
            return events
        for f in sorted(trades_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events


# ---------------------------------------------------------------------------
# run_harness: the main entry point
# ---------------------------------------------------------------------------

def run_harness(
    *,
    broker,
    tmp_path: Path,
    max_cycles: int = 10,
    symbols: Optional[List[str]] = None,
    signals_per_cycle: Optional[List[List[Dict]]] = None,
    run_interval_s: int = 60,
    env_overrides: Optional[Dict[str, str]] = None,
    exec_engine: Optional[_FakeExecEngine] = None,
) -> HarnessResult:
    """Run the runtime loop for exactly *max_cycles* cycles.

    Parameters
    ----------
    broker : ChaosBroker or compatible
        Broker stub to inject.
    tmp_path : Path
        pytest tmp_path for journal/state dirs.
    max_cycles : int
        Force stop after this many cycles.
    symbols : list[str]
        Trading symbols. Default ["SPY"].
    signals_per_cycle : list[list[dict]]
        Per-cycle signal lists emitted by FakeLifecycle.
    run_interval_s : int
        Base cycle interval.
    env_overrides : dict
        Extra env vars to set during the run.
    exec_engine : _FakeExecEngine | None
        Optional pre-built exec engine for call tracking.

    Returns
    -------
    HarnessResult
        Captured run data for assertions.
    """
    import core.runtime.app as app_mod

    symbols = symbols or ["SPY"]
    journal_dir = tmp_path / "journal"

    lifecycle = _FakeLifecycle(signals_per_cycle=signals_per_cycle)
    engine = exec_engine or _FakeExecEngine()
    container = HarnessContainer(
        broker=broker,
        symbols=symbols,
        lifecycle=lifecycle,
        exec_engine=engine,
    )

    clock = FakeClock()
    sleep_recorder = SleepRecorder(clock=clock)

    # Count cycles via a wrapper around the broker's get_clock
    _original_get_clock = getattr(broker, "get_clock", None)
    _cycles_seen = [0]

    def _counting_clock_wrapper():
        """Wrap get_clock to count cycles and stop the loop."""
        result = _original_get_clock()
        _cycles_seen[0] += 1
        # Advance the chaos broker's cycle counter
        if hasattr(broker, "advance_cycle"):
            broker.advance_cycle()
        return result

    # Build env dict
    env = {
        "JOURNAL_DIR": str(journal_dir),
        "MQD_DISABLE_DOTENV": "1",
        "HEARTBEAT_PRINT": "0",
        "DATA_PRINT": "0",
        "BROKER_API_KEY": "FAKE_KEY",
        "BROKER_API_SECRET": "FAKE_SECRET",
        "MAX_CONSECUTIVE_FAILURES": "999",  # don't trip during torture
        "SIGNAL_COOLDOWN_SECONDS": "0",
    }
    if env_overrides:
        env.update(env_overrides)

    error = None
    exit_code = 1

    # We use run_once=False and stop via state.running after max_cycles
    with patch.dict(os.environ, env, clear=False):
        with patch.object(app_mod, "time") as mock_time_module:
            # Patch time.sleep and time.time
            mock_time_module.sleep = sleep_recorder
            mock_time_module.time = lambda: clock.now().timestamp()

            # Patch Container constructor to return our container
            with patch.object(app_mod, "Container", return_value=container):
                # Patch AlpacaBrokerConnector so it returns our broker
                with patch.object(
                    app_mod, "AlpacaBrokerConnector", return_value=broker
                ):
                    # Patch signal.signal to no-op (avoid pytest conflicts)
                    with patch.object(signal, "signal", lambda *a, **kw: None):
                        # Patch _try_recovery to return success
                        with patch.object(
                            app_mod,
                            "_try_recovery",
                            return_value=SimpleNamespace(value="SUCCESS"),
                        ):
                            # The recovery check: `recovery_status == RecoveryStatus.FAILED`
                            # Our mock returns something != FAILED, so it proceeds.
                            # We also need RecoveryStatus.FAILED to not match.
                            from core.recovery.coordinator import RecoveryStatus
                            with patch.object(
                                app_mod,
                                "_try_recovery",
                                return_value=RecoveryStatus.REBUILT,
                            ):
                                # Wrap get_clock for cycle counting
                                if _original_get_clock is not None:
                                    broker.get_clock = _counting_clock_wrapper

                                # Patch _assert_phase1_invariants to no-op
                                with patch.object(
                                    app_mod,
                                    "_assert_phase1_invariants",
                                    lambda **kw: None,
                                ):
                                    # Use run_once=True and call run() max_cycles times
                                    # This is simpler than patching the while loop
                                    opts = app_mod.RunOptions(
                                        config_path=tmp_path / "config.yaml",
                                        mode="paper",
                                        run_interval_s=run_interval_s,
                                        run_once=True,
                                    )

                                    try:
                                        for cycle_idx in range(max_cycles):
                                            # Reset the lifecycle cycle counter
                                            # so it emits the right signals
                                            lifecycle._cycle = cycle_idx

                                            # Advance broker cycle before
                                            # (get_clock wrapper handles it)

                                            rc = app_mod.run(opts)
                                            exit_code = rc

                                            # Advance broker if get_clock wasn't
                                            # called (e.g., no clock method)
                                            if not hasattr(broker, "get_clock"):
                                                if hasattr(broker, "advance_cycle"):
                                                    broker.advance_cycle()

                                    except KeyboardInterrupt:
                                        exit_code = 0
                                    except Exception as exc:
                                        error = exc
                                        exit_code = 1

                                    # Restore original get_clock
                                    if _original_get_clock is not None:
                                        broker.get_clock = _original_get_clock

    return HarnessResult(
        exit_code=exit_code,
        sleep_recorder=sleep_recorder,
        journal_dir=journal_dir,
        container=container,
        broker=broker,
        exec_engine=engine,
        lifecycle=lifecycle,
        error=error,
    )
