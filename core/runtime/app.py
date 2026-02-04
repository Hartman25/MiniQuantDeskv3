"""
Runtime Application - Main trading loop for paper and live trading.

ARCHITECTURE:
- Single unified runner for both paper and live modes
- Event-driven with configurable cycle interval
- Loads strategies from config
- Enforces data validation (anti-lookahead)
- Routes signals through risk gate
- Handles graceful shutdown

PATCH 3: Added live mode halt on reconciliation failures.

Based on LEAN's Algorithm.Run() with enhanced safety.
"""

from __future__ import annotations

import os
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple  # FIX: added Any

import pandas as pd

from core.brokers import AlpacaBrokerConnector, BrokerOrderSide
from core.runtime.circuit_breaker import ConsecutiveFailureBreaker
from core.recovery.coordinator import RecoveryCoordinator, RecoveryStatus
from core.recovery.persistence import StatePersistence
from core.data.contract import MarketDataContract
from core.data.validator import DataValidator
from core.data.pipeline import DataPipelineError
from core.di.container import Container
from core.execution.engine import OrderExecutionEngine
from core.journal.trade_journal import TradeJournal
from core.journal.writer import JournalWriter
from core.logging import LogStream, get_logger
from core.risk.manager import RiskManager
from core.state import OrderStatus
from core.state.position_store import Position

logger = get_logger(LogStream.SYSTEM)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cooldown_key(strategy: str, symbol: str, side: str) -> tuple[str, str, str]:
    return (strategy or "UNKNOWN", symbol, side.upper())


def _cooldown_should_block(
    *,
    last_action_ts: dict[tuple[str, str, str], float],
    key: tuple[str, str, str],
    now_ts: float,
    cooldown_s: int,
) -> tuple[bool, float]:
    if cooldown_s <= 0:
        return (False, 0.0)

    last_ts = float(last_action_ts.get(key, 0.0) or 0.0)
    elapsed = now_ts - last_ts
    return (elapsed < cooldown_s, elapsed)


def _single_trade_should_block_entry(
    symbol: str,
    broker: Optional[Any] = None,
    position_store: Optional[Any] = None,
    order_store: Optional[Any] = None,
    *,
    fail_closed: bool = False,
) -> bool:
    """
    Returns True if we should block a NEW entry because we already have:
      - an open position, or
      - an open order

    IMPORTANT:
      - In tests / paper / fakes, many methods may not exist.
      - Default behavior here is FAIL-OPEN (return False) if we cannot determine state.
      - Set fail_closed=True for live safety if you want “block on uncertainty”.
    """

    sym = str(symbol).upper().strip()

    def _qty_nonzero(pos: Any) -> bool:
        if pos is None:
            return False
        # dict style
        if isinstance(pos, dict):
            q = pos.get("qty") or pos.get("quantity") or pos.get("position_qty")
        else:
            q = getattr(pos, "qty", None)
            if q is None:
                q = getattr(pos, "quantity", None)
        if q is None:
            return True  # position exists but qty unknown -> treat as open (conservative)
        try:
            return float(q) != 0.0
        except Exception:
            return bool(q)

    try:
        # ---- check local position store (preferred in unit tests) ----
        if position_store is not None:
            if hasattr(position_store, "has_open_position"):
                if position_store.has_open_position(sym):
                    return True
            elif hasattr(position_store, "get_position"):
                if _qty_nonzero(position_store.get_position(sym)):
                    return True

        # ---- check local order store (preferred in unit tests) ----
        if order_store is not None:
            if hasattr(order_store, "has_open_order"):
                if order_store.has_open_order(sym):
                    return True
            elif hasattr(order_store, "get_open_orders"):
                oo = order_store.get_open_orders(sym)
                if oo:
                    return True

        # ---- check broker connector (best-effort; may not exist in tests) ----
        if broker is not None:
            # positions
            if hasattr(broker, "list_positions"):
                positions = broker.list_positions()
                for p in positions or []:
                    psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None))
                    if str(psym).upper() == sym and _qty_nonzero(p):
                        return True
            elif hasattr(broker, "get_positions"):
                positions = broker.get_positions()
                for p in positions or []:
                    psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None))
                    if str(psym).upper() == sym and _qty_nonzero(p):
                        return True

            # open orders
            if hasattr(broker, "list_open_orders"):
                orders = broker.list_open_orders()
                for o in orders or []:
                    osym = (o.get("symbol") if isinstance(o, dict) else getattr(o, "symbol", None))
                    if str(osym).upper() == sym:
                        return True
            elif hasattr(broker, "list_orders"):
                # some APIs require params; if so, we just fail-open
                try:
                    orders = broker.list_orders(status="open")
                except TypeError:
                    orders = broker.list_orders()
                for o in orders or []:
                    osym = (o.get("symbol") if isinstance(o, dict) else getattr(o, "symbol", None))
                    if str(osym).upper() == sym:
                        return True

        return False

    except Exception:
        # fail-open for tests/paper, fail-closed if explicitly requested
        return True if fail_closed else False


def _is_exit_signal(sig: dict) -> bool:
    """
    Treat explicit exits as non-entry signals.
    Supported:
      - action in {"EXIT","CLOSE","SELL"} when you are long-only
      - side == "SELL" when you are long-only
      - intent == "exit"
      - reduce_only == True
    """
    action = str(sig.get("action") or "").upper()
    side = str(sig.get("side") or "").upper()
    intent = str(sig.get("intent") or "").lower()
    reduce_only = bool(sig.get("reduce_only") or False)

    if intent == "exit":
        return True
    if reduce_only:
        return True
    if action in {"EXIT", "CLOSE"}:
        return True

    # If your system is LONG-only, SELL is treated as exit (check both action and side)
    if action == "SELL" or side == "SELL":
        return True

    return False


def _try_recovery(broker, position_store, order_machine, state_dir: Optional[Path] = None) -> RecoveryStatus:
    """
    P1 Patch 7: Attempt crash-recovery via RecoveryCoordinator.

    Returns RecoveryStatus. If FAILED, the caller should halt.
    On any internal exception, returns REBUILT (fail-open so the
    loop can still start).
    """
    try:
        sdir = state_dir or Path(os.getenv("STATE_DIR", "data/state"))
        persistence = StatePersistence(state_dir=sdir)
        coord = RecoveryCoordinator(
            persistence=persistence,
            broker=broker,
            position_store=position_store,
            order_machine=order_machine,
        )
        report = coord.recover()
        logger.info(
            "Recovery complete: %s (positions recovered=%d rebuilt=%d)",
            report.status.value,
            report.positions_recovered,
            report.positions_rebuilt,
        )
        return report.status
    except Exception:
        logger.warning("Recovery coordinator raised; continuing with REBUILT", exc_info=True)
        return RecoveryStatus.REBUILT


def _load_protective_stops_from_broker(broker) -> Dict[str, str]:
    """
    P1 Patch 3: On restart, query the broker for open STOP SELL orders
    and return {symbol: broker_order_id} so that protective_stop_ids
    can be rebuilt.

    Returns empty dict on any failure (fail-open: safe because the
    worst case is placing a duplicate stop, which the single-trade guard
    will block anyway).
    """
    result: Dict[str, str] = {}
    try:
        if not hasattr(broker, "list_open_orders"):
            return result
        open_orders = broker.list_open_orders() or []
        for o in open_orders:
            if isinstance(o, dict):
                otype = str(o.get("order_type", "")).lower()
                oside = str(o.get("side", "")).lower()
                osym = o.get("symbol", "")
                oid = o.get("id", "")
            else:
                otype = str(getattr(o, "order_type", "")).lower()
                oside = str(getattr(o, "side", "")).lower()
                osym = getattr(o, "symbol", "")
                oid = getattr(o, "id", "")
            if otype == "stop" and oside == "sell" and osym and oid:
                result[str(osym).upper()] = str(oid)
    except Exception:
        pass
    return result


def _emit_limit_ttl_cancel_event(
    *,
    journal,
    run_id: str | None,
    internal_order_id: str,
    broker_order_id: str,
    symbol: str,
    side: str,
    qty: str,
    order_type: str,
    limit_price: str | None,
    strategy: str,
    ttl_seconds: int,
    final_status: str,
    reason: str,
) -> None:
    """
    PATCH 2.3: canonical journal event schema for TTL-cancelled limit entries.
    """
    event = {
        "event": "ORDER_TTL_CANCEL",
        "ts_utc": _utc_iso(),
        "run_id": run_id,
        "internal_order_id": internal_order_id,
        "broker_order_id": broker_order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "order_type": order_type,
        "limit_price": limit_price,
        "strategy": strategy,
        "ttl_seconds": ttl_seconds,
        "final_status": final_status,
        "reason": reason,
    }
    journal.write_event(event)


def _emit_auto_heal_event(
    *,
    journal,
    run_id: str | None,
    discrepancy,
    action: str,
    strategy: str | None = None,
) -> None:
    """
    PATCH 2.4: canonical paper auto-heal event schema.
    """
    journal.write_event(
        {
            "event": "AUTO_HEAL_APPLIED",
            "ts_utc": _utc_iso(),
            "run_id": run_id,
            "discrepancy_type": getattr(discrepancy, "type", None),
            "symbol": getattr(discrepancy, "symbol", None),
            "local_value": getattr(discrepancy, "local_value", None),
            "broker_value": getattr(discrepancy, "broker_value", None),
            "action": action,
            "resolution": "paper_auto_heal",
            "strategy": strategy,
        }
    )


@dataclass
class RunOptions:
    config_path: Path
    mode: str  # "paper" or "live"
    run_interval_s: int = 60
    run_once: bool = False


def _safe_decimal(v, default: Decimal = Decimal("0")) -> Decimal:
    """
    Convert v to Decimal safely. Prevents runtime loop crashes when mocks or
    unexpected types are passed during tests.
    """
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _ensure_strategy_registry_bootstrapped(container: Container) -> None:
    """
    Register built-in strategies into StrategyRegistry.
    """
    registry = container.get_strategy_registry()

    try:
        from strategies.vwap_mean_reversion import VWAPMeanReversion
        registry.register(VWAPMeanReversion)

        from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
        registry.register(VWAPMicroMeanReversion)
    except ValueError:
        # Already registered
        pass
    except Exception as e:
        raise RuntimeError(f"Failed to register built-in strategies: {e}") from e


def _df_to_contracts(symbol: str, df: pd.DataFrame) -> List[MarketDataContract]:
    """Convert provider DataFrame to MarketDataContract list."""
    bars: List[MarketDataContract] = []
    if df is None or df.empty:
        return bars

    for ts, row in df.iterrows():
        try:
            bars.append(
                MarketDataContract(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(row["volume"]) if "volume" in row and pd.notna(row["volume"]) else None,
                    provider="alpaca",
                )
            )
        except Exception:
            continue

    return bars



def _get_latest_bars_compat(data_pipeline, symbol: str, lookback: int, timeframe: str):
    """Call get_latest_bars across multiple provider signatures (tests use stubs)."""
    fn = getattr(data_pipeline, "get_latest_bars", None)
    if fn is None:
        raise AttributeError("data_pipeline has no get_latest_bars()")
    # Prefer keyword forms if accepted
    # 1) lookback_bars + timeframe
    try:
        return fn(symbol, lookback_bars=lookback, timeframe=timeframe)
    except TypeError:
        pass
    # 2) lookback + timeframe keywords
    try:
        return fn(symbol, lookback=lookback, timeframe=timeframe)
    except TypeError:
        pass
    # 3) positional (symbol, lookback)
    try:
        return fn(symbol, lookback)
    except TypeError:
        pass
    # 4) positional (symbol, timeframe)
    try:
        return fn(symbol, timeframe)
    except TypeError:
        pass
    # 5) positional (symbol)
    return fn(symbol)

def _to_dt(ts) -> datetime:
    """Best-effort normalize timestamps to tz-aware UTC datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except Exception:
        return datetime.now(tz=timezone.utc)


def run(opts: RunOptions) -> int:
    """Run the trading app. Returns exit code: 0 success, 1 safety halt/failure."""
    container = Container()
    container.initialize(opts.config_path)

    cfg = container.get_config()

    paper = opts.mode == "paper"
    cfg.broker.paper_trading = paper

    broker = AlpacaBrokerConnector(
        api_key=cfg.broker.api_key,
        api_secret=cfg.broker.api_secret,
        paper=paper,
    )
    container.set_broker_connector(broker)

    # Register strategies
    _ensure_strategy_registry_bootstrapped(container)

    # Start services
    container.start()

    # ===============================================================
    # PATCH 5: Unified Journal (JSONL) for audit/ML
    # ===============================================================
    journal_dir = Path(os.getenv("JOURNAL_DIR", "data/journal"))
    journal = JournalWriter(base_dir=journal_dir)
    journal.write_event({"event": "boot", "mode": opts.mode, "paper": paper})

    # ===============================================================
    # PATCH 1: Canonical Trade Journal (trade lifecycle, schema-versioned)
    # ===============================================================
    trade_journal = TradeJournal(base_dir=journal_dir)
    trade_run_id = TradeJournal.new_run_id()

    try:
        protections = container.get_protections()
        logger.info("Using unified ProtectionManager from container (5 protections active)")

        registry = container.get_strategy_registry()
        lifecycle = container.get_strategy_lifecycle()

        exec_engine: OrderExecutionEngine = container.get_order_execution_engine()

        if exec_engine and hasattr(exec_engine, "set_trade_journal"):
            exec_engine.set_trade_journal(trade_journal, run_id=trade_run_id)

        risk_manager = container.get_risk_manager()
        data_validator = container.get_data_validator()
        # Data pipeline: prefer a dedicated MarketDataPipeline if the container provides it,
        # otherwise fall back to a provider-style interface (used by some test harnesses).
        get_pipeline = getattr(container, 'get_data_pipeline', None)
        if callable(get_pipeline):
            data_pipeline = get_pipeline()
        else:
            get_provider = getattr(container, 'get_data_provider', None)
            if not callable(get_provider):
                raise RuntimeError('Container must provide get_data_pipeline() or get_data_provider()')
            data_pipeline = get_provider()
        position_store = container.get_position_store()  # FIX: Get before use in guards

        # ===============================================================
        # P1 Patch 7: Recovery coordinator – reconstruct state on startup
        # ===============================================================
        _order_machine = getattr(exec_engine, "state_machine", None) if exec_engine else None
        recovery_status = _try_recovery(broker, position_store, _order_machine)
        if recovery_status == RecoveryStatus.FAILED:
            logger.error("RECOVERY FAILED – halting runtime for safety")
            return 1

        # ===============================================================
        # PATCH 3 SAFETY: In LIVE mode, discrepancies at startup must halt
        # ===============================================================
        if opts.mode == "live":
            try:
                reconciler = container.get_reconciler()
                discrepancies = reconciler.reconcile_startup()
            except Exception as e:
                logger.exception("Startup reconciliation failed in LIVE mode: %s", e)
                return 1

            if discrepancies:
                logger.error("LIVE MODE HALT: startup reconciliation found discrepancies: %s", discrepancies)
                return 1

        # ===============================================================
        # PATCH 2.1 + 2.4: Paper-mode startup reconcile (log-only or auto-heal)
        # ===============================================================
        if opts.mode == "paper" and hasattr(container, "get_reconciler"):
            reconciler = container.get_reconciler()
            try:
                discrepancies = reconciler.reconcile_startup()
            except Exception as e:
                logger.exception("Paper startup reconciliation failed: %s", e)
                discrepancies = []

            if discrepancies:
                if hasattr(reconciler, "heal_startup"):
                    try:
                        reconciler.heal_startup(discrepancies)
                    except Exception as e:
                        logger.exception("Paper reconcile: heal_startup failed: %s", e)

                paper_heal_env = str(os.getenv("PAPER_AUTO_HEAL", "")).strip().lower()
                if not paper_heal_env:
                    paper_heal_env = str(os.getenv("AUTO_HEAL", "0")).strip().lower()
                auto_heal = paper_heal_env in ("1", "true", "yes")

                if auto_heal and hasattr(reconciler, "auto_heal"):
                    try:
                        journal.write_event({"event": "auto_heal_started", "count": len(discrepancies)})
                        healed = reconciler.auto_heal(discrepancies)
                        journal.write_event({"event": "auto_heal_completed", "result": healed})
                        logger.warning("Paper reconcile: auto-heal applied to %d discrepancies", len(discrepancies))
                    except Exception as e:
                        journal.write_event({"event": "auto_heal_failed", "error": str(e)})
                        logger.exception("Paper reconcile: auto-heal failed: %s", e)
                else:
                    journal.write_event({"event": "startup_reconcile_discrepancies", "count": len(discrepancies)})
                    logger.warning("Paper reconcile found %d discrepancies (auto-heal disabled)", len(discrepancies))

        elif opts.mode == "paper":
            logger.info("Paper startup reconcile skipped (no reconciler in container)")

        # Optional: dynamic universe
        universe_mode = os.getenv("UNIVERSE_MODE")  # hybrid|accepted|scanner
        universe_symbols: Optional[List[str]] = None
        if universe_mode:
            try:
                from core.universe import get_universe_symbols
                universe_symbols = get_universe_symbols(mode=universe_mode)
            except Exception as e:
                logger.warning(f"Universe mode '{universe_mode}' requested but unavailable: {e}")

        all_symbols: List[str] = []
        strategies_obj = cfg.strategies

        if isinstance(strategies_obj, list):
            candidates = strategies_obj
        else:
            candidates = getattr(strategies_obj, "enabled", [])

        enabled_strategies = []
        for s in candidates:
            if isinstance(s, dict):
                is_enabled = s.get("enabled", True)
            else:
                is_enabled = getattr(s, "enabled", True)
            if is_enabled:
                enabled_strategies.append(s)

        for strat_cfg in enabled_strategies:
            if isinstance(strat_cfg, dict):
                name = strat_cfg.get("name")
                config = strat_cfg.get("config", {}) or {}
                symbols = strat_cfg.get("symbols", []) or []
                timeframe = strat_cfg.get("timeframe", "1Min") or "1Min"
            else:
                name = getattr(strat_cfg, "name", None)
                config = getattr(strat_cfg, "config", {}) or {}
                symbols = getattr(strat_cfg, "symbols", []) or []
                timeframe = getattr(strat_cfg, "timeframe", "1Min") or "1Min"

            if not name:
                logger.warning("Skipping strategy with missing name", extra={"strat_cfg": str(strat_cfg)})
                continue

            s = registry.create(
                name=name,
                config=config,
                symbols=(universe_symbols or symbols),
                timeframe=timeframe,
            )
            lifecycle.add_strategy(s)
            lifecycle.start_strategy(s.name)
            for sym in s.symbols:
                if sym not in all_symbols:
                    all_symbols.append(sym)

        # ===============================================================
        # SIGNAL HANDLING
        # ===============================================================
        class _State:
            running = True

        state = _State()

        def _stop(_sig, _frame):
            state.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        logger.info(f"Starting runtime loop mode={opts.mode} symbols={all_symbols}")

        cycle_count = 0
        orphan_check_interval = 10

        # P1 Patch 3: reload protective stops from broker on restart
        protective_stop_ids: Dict[str, str] = _load_protective_stops_from_broker(broker)
        if protective_stop_ids:
            logger.info("Reloaded %d protective stop(s) from broker", len(protective_stop_ids))

        _open_guard_cache: dict[str, dict] = {}  # optional memo per loop iteration
        cooldown_s = int(os.getenv("SIGNAL_COOLDOWN_SECONDS", "30") or "30")
        last_action_ts: Dict[Tuple[str, str, str], float] = {}

        # P1 Patch 1: circuit breaker – halt after N consecutive loop failures
        _max_consecutive = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "5") or "5")
        _circuit_breaker = ConsecutiveFailureBreaker(max_failures=_max_consecutive)

        while state.running:
            try:
                acct = broker.get_account_info()
                account_value = _safe_decimal(acct.get("portfolio_value", "0"))
                buying_power = _safe_decimal(acct.get("buying_power", "0"))

                for symbol in all_symbols:
                    timeframe = "1Min"
                    lookback = 120

                    try:
                        df = _get_latest_bars_compat(data_pipeline, symbol, lookback, timeframe)
                    except DataPipelineError as e:
                        journal.write_event({'event': 'market_data_block', 'symbol': symbol, 'reason': str(e)})
                        logger.warning('Market data blocked; skipping symbol', extra={'symbol': symbol, 'error': str(e)})
                        continue
                    except Exception as e:
                        journal.write_event({'event': 'market_data_error', 'symbol': symbol, 'error': str(e)})
                        logger.exception('Market data error; skipping symbol', extra={'symbol': symbol})
                        continue
                    bars = _df_to_contracts(symbol, df)

                    data_validator.validate_bars(bars=bars, timeframe=timeframe)

                    bar = bars[-1]
                    if opts.mode == "live" and not bar.is_complete(timeframe):
                        continue

                    signals = lifecycle.on_bar(bar)

                    for sig in signals:
                        trade_id = sig.get("trade_id") or (
                            f"{sig.get('strategy','UNKNOWN')}:{sig.get('symbol', symbol)}:"
                            f"{datetime.now(timezone.utc).date().isoformat()}:{uuid.uuid4().hex[:10]}"
                        )
                        sig["trade_id"] = trade_id
                        journal.write_event({"event": "signal_received", "trade_id": trade_id, "signal": dict(sig)})

                        sig_symbol = sig.get("symbol", symbol)
                        side_str = str(sig.get("side", "BUY")).upper()
                        qty = Decimal(str(sig.get("quantity", "0")))
                        if qty <= 0:
                            continue

                        sig_limit = sig.get("limit_price")
                        sig_price = Decimal(str(sig_limit)) if sig_limit is not None else Decimal(str(sig.get("price", bar.close)))
                        sig_strategy = sig.get("strategy", "UNKNOWN")

                        # PATCH 2.6: single-trade-at-a-time guard (block entries if position or open order exists)
                        if not _is_exit_signal(sig):
                            try:
                                open_orders = []
                                if hasattr(exec_engine, "get_open_orders"):
                                    open_orders = exec_engine.get_open_orders(symbol=sig_symbol) or []
                                elif hasattr(container, "get_order_tracker"):
                                    tracker = container.get_order_tracker()
                                    if hasattr(tracker, "get_open_orders_for_symbol"):
                                        open_orders = tracker.get_open_orders_for_symbol(sig_symbol) or []

                                pos_qty = None
                                if hasattr(position_store, "get_position"):
                                    p = position_store.get_position(sig_symbol)
                                    pos_qty = getattr(p, "qty", None) if p is not None else None

                                if pos_qty is None and hasattr(position_store, "get_all_positions"):
                                    allp = position_store.get_all_positions() or []
                                    for p in allp:
                                        sym = getattr(p, "symbol", None)
                                        if sym == sig_symbol:
                                            pos_qty = getattr(p, "qty", None)
                                            break

                                has_position = False
                                if pos_qty is not None:
                                    try:
                                        has_position = float(pos_qty) != 0.0
                                    except Exception:
                                        has_position = True

                                has_open_order = bool(open_orders)

                                if has_position or has_open_order:
                                    journal.write_event({
                                        "event": "single_trade_block",
                                        "ts_utc": _utc_iso(),
                                        "trade_id": trade_id,
                                        "strategy": sig_strategy,
                                        "symbol": sig_symbol,
                                        "side": side_str,
                                        "qty": str(qty),
                                        "has_position": has_position,
                                        "has_open_order": has_open_order,
                                        "reason": "position_or_open_order_exists",
                                    })
                                    continue

                            except Exception as e:
                                journal.write_event({
                                    "event": "single_trade_block",
                                    "ts_utc": _utc_iso(),
                                    "trade_id": trade_id,
                                    "strategy": sig_strategy,
                                    "symbol": sig_symbol,
                                    "side": side_str,
                                    "qty": str(qty),
                                    "has_position": None,
                                    "has_open_order": None,
                                    "reason": f"guard_error:{type(e).__name__}",
                                })
                                continue

                        # PATCH 2.5: runtime cooldown gate (anti-spam / idempotency)
                        key = _cooldown_key(sig_strategy, sig_symbol, side_str)
                        now_ts = time.time()
                        last_ts = last_action_ts.get(key, 0.0)
                        if cooldown_s > 0 and (now_ts - last_ts) < cooldown_s:
                            journal.write_event({
                                "event": "signal_cooldown_block",
                                "ts_utc": _utc_iso(),
                                "trade_id": trade_id,
                                "strategy": sig_strategy,
                                "symbol": sig_symbol,
                                "side": side_str,
                                "qty": str(qty),
                                "cooldown_seconds": cooldown_s,
                                "elapsed_seconds": round(now_ts - last_ts, 3),
                                "reason": "cooldown_active",
                            })
                            continue

                        prot_result = protections.check(
                            symbol=sig_symbol,
                            current_trades=None,
                            completed_trades=None,
                        )

                        if prot_result.is_protected:
                            journal.write_event(
                                {
                                    "event": "protection_block",
                                    "trade_id": trade_id,
                                    "symbol": sig_symbol,
                                    "side": side_str,
                                    "qty": str(qty),
                                    "reason": prot_result.reason,
                                }
                            )
                            logger.warning(
                                f"PROTECTION_BLOCK: {sig_symbol} {side_str} qty={qty} reason={prot_result.reason}",
                                extra={
                                    "strategy": sig_strategy,
                                    "symbol": sig_symbol,
                                    "protection_reason": prot_result.reason,
                                    "protected_until": prot_result.until.isoformat() if prot_result.until else None,
                                },
                            )
                            continue

                        broker_side = BrokerOrderSide.BUY if side_str in ("BUY", "LONG") else BrokerOrderSide.SELL

                        risk = risk_manager.validate_trade(
                            symbol=sig_symbol,
                            quantity=qty,
                            side=broker_side,
                            price=sig_price,
                            account_value=account_value,
                            buying_power=buying_power,
                            strategy=sig_strategy,
                        )

                        journal.write_event(
                            {
                                "event": "risk_decision",
                                "trade_id": trade_id,
                                "approved": bool(getattr(risk, "approved", False)),
                                "reason": getattr(risk, "reason", None),
                                "details": (risk.to_dict() if hasattr(risk, "to_dict") else {}),
                            }
                        )

                        if not risk.approved:
                            logger.warning(
                                f"RISK_BLOCK: {sig_symbol} {side_str} qty={qty} reason={risk.reason}",
                                extra=risk.to_dict(),
                            )
                            continue

                        try:
                            if broker_side == BrokerOrderSide.BUY:
                                for _attr in (
                                    "approved_qty",
                                    "approved_quantity",
                                    "capped_qty",
                                    "capped_quantity",
                                    "sized_qty",
                                    "sized_quantity",
                                ):
                                    if hasattr(risk, _attr):
                                        _v = getattr(risk, _attr)
                                        if _v is not None:
                                            qty = Decimal(str(_v))
                                            break

                            if hasattr(broker, "get_position"):
                                try:
                                    pos = broker.get_position(sig_symbol)
                                except Exception:
                                    pos = None

                                if pos is not None:
                                    pos_qty_raw = None
                                    for _k in ("qty", "quantity", "position_qty"):
                                        if hasattr(pos, _k):
                                            pos_qty_raw = getattr(pos, _k)
                                            break
                                        if isinstance(pos, dict) and _k in pos:
                                            pos_qty_raw = pos.get(_k)
                                            break

                                    pos_qty = Decimal(str(pos_qty_raw)) if pos_qty_raw is not None else Decimal("0")

                                    if broker_side == BrokerOrderSide.BUY and pos_qty > 0:
                                        logger.info(
                                            "RISK_SKIP_ALREADY_IN_POSITION",
                                            extra={"symbol": sig_symbol, "pos_qty": str(pos_qty), "requested_qty": str(qty)},
                                        )
                                        continue

                                    if broker_side == BrokerOrderSide.SELL:
                                        if pos_qty <= 0:
                                            logger.info("RISK_SKIP_NO_POSITION_TO_SELL", extra={"symbol": sig_symbol})
                                            continue
                                        if qty > pos_qty:
                                            logger.info(
                                                "RISK_CAP_SELL_QTY_TO_POSITION",
                                                extra={"symbol": sig_symbol, "requested_qty": str(qty), "pos_qty": str(pos_qty)},
                                            )
                                            qty = pos_qty

                            if qty <= 0:
                                logger.info("RISK_SKIP_NONPOSITIVE_QTY", extra={"symbol": sig_symbol, "qty": str(qty)})
                                continue
                        except Exception:
                            logger.exception("Risk enforcement failed; skipping trade for safety", extra={"symbol": sig_symbol})
                            continue

                        if broker_side == BrokerOrderSide.SELL:
                            stop_id = protective_stop_ids.get(sig_symbol)
                            if stop_id:
                                journal.write_event(
                                    {
                                        "event": "protective_stop_cancel_requested",
                                        "trade_id": trade_id,
                                        "symbol": sig_symbol,
                                        "stop_broker_order_id": stop_id,
                                        "reason": "exit_signal_cancel_protective_stop",
                                    }
                                )
                                try:
                                    exec_engine.cancel_order(
                                        internal_order_id=f"{sig_strategy}-STOPCXL-{uuid.uuid4().hex[:6]}",
                                        broker_order_id=stop_id,
                                        reason="exit_signal_cancel_protective_stop",
                                    )
                                except Exception:
                                    logger.warning("Failed cancelling protective stop", exc_info=True)
                                protective_stop_ids.pop(sig_symbol, None)

                        internal_id = f"{sig_strategy}-{uuid.uuid4().hex[:10]}"

                        stop_loss = sig.get("stop_loss")
                        take_profit = sig.get("take_profit")
                        if stop_loss is not None:
                            stop_loss = Decimal(str(stop_loss))
                        if take_profit is not None:
                            take_profit = Decimal(str(take_profit))

                        order_type = str(sig.get("order_type", "MARKET")).upper()
                        ttl_seconds = int(sig.get("ttl_seconds") or 90)

                        broker_order_id: Optional[str] = None

                        if order_type == "LIMIT":
                            limit_price = sig.get("limit_price")
                            if limit_price is None:
                                logger.warning(f"LIMIT signal missing limit_price; skipping order: {sig}")
                                continue

                            limit_price_d = Decimal(str(limit_price))
                            last_action_ts[key] = now_ts
                            broker_order_id = exec_engine.submit_limit_order(
                                internal_order_id=internal_id,
                                symbol=sig_symbol,
                                quantity=qty,
                                side=broker_side,
                                limit_price=limit_price_d,
                                strategy=sig_strategy,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )

                            journal.write_event(
                                {
                                    "event": "order_submitted",
                                    "internal_order_id": internal_id,
                                    "broker_order_id": broker_order_id,
                                    "symbol": sig_symbol,
                                    "side": str(broker_side),
                                    "qty": str(qty),
                                    "order_type": "LIMIT",
                                    "limit_price": str(limit_price_d),
                                    "strategy": sig_strategy,
                                    "ttl_seconds": ttl_seconds,
                                }
                            )

                            final_status = exec_engine.wait_for_order(
                                internal_order_id=internal_id,
                                broker_order_id=broker_order_id,
                                timeout_seconds=ttl_seconds,
                                poll_interval=2.0,
                            )

                            if final_status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                                try:
                                    exec_engine.cancel_order(
                                        internal_order_id=internal_id,
                                        broker_order_id=broker_order_id,
                                        reason="limit_ttl_expired_no_chase",
                                    )
                                except Exception:
                                    pass

                                run_id_opt = getattr(container, "run_id", None)
                                if callable(run_id_opt):
                                    try:
                                        run_id_opt = run_id_opt()
                                    except Exception:
                                        run_id_opt = None

                                _emit_limit_ttl_cancel_event(
                                    journal=journal,
                                    run_id=run_id_opt if isinstance(run_id_opt, str) else None,
                                    internal_order_id=internal_id,
                                    broker_order_id=broker_order_id,
                                    symbol=sig_symbol,
                                    side=str(broker_side),
                                    qty=str(qty),
                                    order_type="LIMIT",
                                    limit_price=str(limit_price_d),
                                    strategy=sig_strategy,
                                    ttl_seconds=ttl_seconds,
                                    final_status=getattr(final_status, "value", str(final_status)),
                                    reason="limit_ttl_expired_no_chase",
                                )
                                continue

                        else:
                            last_action_ts[key] = now_ts
                            broker_order_id = exec_engine.submit_market_order(
                                internal_order_id=internal_id,
                                symbol=sig_symbol,
                                quantity=qty,
                                side=broker_side,
                                strategy=sig_strategy,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )

                            journal.write_event(
                                {
                                    "event": "order_submitted",
                                    "trade_id": trade_id,
                                    "internal_order_id": internal_id,
                                    "broker_order_id": broker_order_id,
                                    "symbol": sig_symbol,
                                    "side": side_str,
                                    "qty": str(qty),
                                    "order_type": "MARKET",
                                    "strategy": sig_strategy,
                                }
                            )

                            exec_engine.wait_for_order(
                                internal_order_id=internal_id,
                                broker_order_id=broker_order_id,
                                timeout_seconds=15,
                                poll_interval=1.0,
                            )

                        filled_qty, fill_price = exec_engine.get_fill_details(internal_id)
                        if filled_qty is None or fill_price is None:
                            try:
                                status, fill_info = broker.get_order_status(broker_order_id)
                                if fill_info:
                                    filled_qty = fill_info.get("filled_qty")
                                    fill_price = fill_info.get("filled_avg_price")
                            except Exception:
                                pass

                        stop_price = None

                        if broker_side == BrokerOrderSide.BUY:
                            for k in ("stop_loss", "stop_loss_price", "stop_price"):
                                if isinstance(sig, dict) and sig.get(k) is not None:
                                    stop_price = sig.get(k)
                                    break
                                if hasattr(sig, k) and getattr(sig, k) is not None:
                                    stop_price = getattr(sig, k)
                                    break

                            if stop_price is not None and filled_qty is not None:
                                try:
                                    stop_price_dec = Decimal(str(stop_price))
                                    stop_id = exec_engine.submit_stop_order(
                                        internal_order_id=f"{sig_strategy}-STOP-{uuid.uuid4().hex[:6]}",
                                        symbol=sig_symbol,
                                        side=BrokerOrderSide.SELL,
                                        quantity=filled_qty,
                                        stop_price=stop_price_dec,
                                        reason="protective_stop_after_entry",
                                    )
                                    protective_stop_ids[sig_symbol] = stop_id
                                    journal.write_event(
                                        {
                                            "event": "protective_stop_submitted",
                                            "trade_id": trade_id,
                                            "symbol": sig_symbol,
                                            "stop_broker_order_id": stop_id,
                                            "stop_price": str(stop_price_dec),
                                            "qty": str(filled_qty),
                                        }
                                    )
                                except Exception:
                                    logger.exception(
                                        "Failed to place protective STOP",
                                        extra={"symbol": sig_symbol, "stop_price": str(stop_price), "filled_qty": str(filled_qty)},
                                    )

                        if filled_qty is not None and fill_price is not None:
                            journal.write_event(
                                {
                                    "event": "order_filled",
                                    "trade_id": trade_id,
                                    "internal_order_id": internal_id,
                                    "broker_order_id": broker_order_id,
                                    "symbol": sig_symbol,
                                    "side": side_str,
                                    "filled_qty": str(filled_qty),
                                    "fill_price": str(fill_price),
                                }
                            )

                            try:
                                lifecycle.on_order_filled(
                                    strategy_name=sig_strategy,
                                    order_id=internal_id,
                                    symbol=sig_symbol,
                                    filled_qty=filled_qty,
                                    fill_price=fill_price,
                                )
                            except Exception:
                                logger.warning("Strategy fill callback failed", exc_info=True)

                            try:
                                position_store = container.get_position_store()
                                if broker_side == BrokerOrderSide.BUY:
                                    pos = Position(
                                        symbol=sig_symbol,
                                        quantity=filled_qty,
                                        entry_price=fill_price,
                                        entry_time=datetime.now(timezone.utc),
                                        strategy=sig_strategy,
                                        order_id=internal_id,
                                        stop_loss=stop_loss,
                                        take_profit=take_profit,
                                    )
                                    position_store.upsert(pos)
                                else:
                                    position_store.delete(sig_symbol)
                            except Exception:
                                logger.warning("PositionStore update failed", exc_info=True)

                cycle_count += 1
                if cycle_count >= orphan_check_interval:
                    cycle_count = 0
                    try:
                        order_tracker = container.get_order_tracker()
                        broker_orders_list = broker.get_orders()
                        broker_orders = {order.id: order for order in broker_orders_list}

                        orphans = order_tracker.get_orphaned_orders(broker_orders)
                        shadows = order_tracker.get_shadow_orders(broker_orders)

                        if orphans:
                            logger.error(
                                f"ORPHAN ORDERS DETECTED: {len(orphans)} orders",
                                extra={"orphan_broker_ids": orphans, "action": "Manual review required"},
                            )
                        if shadows:
                            logger.error(
                                f"SHADOW ORDERS DETECTED: {len(shadows)} orders",
                                extra={"shadow_client_ids": shadows, "action": "Manual review required"},
                            )
                        if not orphans and not shadows:
                            logger.info("Orphan check: No drift detected")
                    except Exception as e:
                        logger.error(f"Orphan check failed: {e}", exc_info=True)

                # ✅ success path only: reset breaker after a full successful cycle
                _circuit_breaker.record_success()

                if opts.run_once:
                    state.running = False
                    break

                if opts.run_interval_s > 0:
                    time.sleep(opts.run_interval_s)

            except Exception as e:
                try:
                    journal.write_event({"event": "runtime_error", "error": str(e)})
                except Exception:
                    pass
                logger.exception(f"Runtime loop error: {e}")

                _circuit_breaker.record_failure()
                if _circuit_breaker.is_tripped:
                    logger.error(
                        "CIRCUIT BREAKER TRIPPED: %d consecutive failures – halting",
                        _circuit_breaker.failure_count,
                    )
                    return 1

                if opts.run_once:
                    return 1

                if opts.run_interval_s > 0:
                    time.sleep(opts.run_interval_s)

        logger.info("Runtime loop stopped")
        return 0

    finally:
        try:
            container.stop()
        except Exception:
            pass

        try:
            trade_journal.close()
        except Exception:
            pass


def run_app(opts: RunOptions) -> int:
    """
    Public entrypoint used by tests. MUST return an int exit code.
    0 = success / completed
    1 = halted due to safety condition or runtime failure
    """
    try:
        return run(opts)
    except Exception as e:
        logger.exception("Fatal error in run_app: %s", e)
        return 1
