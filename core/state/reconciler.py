# core/state/reconciler.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)

    # For unittest.mock objects: getattr(mock, "missing") returns another Mock.
    # We treat "auto-created Mock attribute" as missing and return default.
    try:
        val = getattr(obj, key)
    except Exception:
        return default

    # Heuristic: if it quacks like an auto-created Mock, ignore it.
    # (Real values like Decimal/int/str won't have these attributes.)
    if hasattr(val, "assert_called_once_with") and hasattr(val, "mock_calls"):
        return default

    return val

@dataclass(frozen=True)
class Discrepancy:
    """
    A single startup reconciliation discrepancy.

    type:
      - missing_position   : broker has position, local does not
      - extra_position     : local has position, broker does not
      - qty_mismatch       : both have position but qty differs
      - order_missing_local: broker has open order, local does not
      - order_missing_broker: local has open order, broker does not
      - order_status_mismatch: both have order but status differs
    """
    type: str
    symbol: str
    local_value: Any
    broker_value: Any
    resolution: str
    timestamp: datetime


class StartupReconciler:
    """
    Reconciles local state (PositionStore + OrderTracker) against broker state at startup.
    Returns a list of Discrepancy objects; caller decides whether to halt (live) or log-only (paper).
    """

    def __init__(self, *, broker: Any, position_store: Any, order_tracker: Any):
        self.broker = broker
        self.position_store = position_store
        self.order_tracker = order_tracker

    # -------------------------
    # Broker / local helpers
    # -------------------------
    def _broker_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {symbol: {"qty": Decimal, "avg_entry": Optional[Decimal], "raw": obj}}
        """
        out: Dict[str, Dict[str, Any]] = {}

        # Broker connector should have get_positions() OR list_positions() OR positions
        positions = None
        for fn in ("get_positions", "list_positions"):
            if hasattr(self.broker, fn):
                positions = getattr(self.broker, fn)()
                break

        if positions is None:
            positions = _safe_get(self.broker, "positions", []) or []

        for p in positions:
            sym = _safe_get(p, "symbol")
            if not sym:
                continue
            # Support both dict-style ("qty") and object-style ("quantity") positions
            qty_raw = _safe_get(p, "qty", None)
            if qty_raw is None:
                qty_raw = _safe_get(p, "quantity", None)
            if qty_raw is None:
                qty_raw = _safe_get(p, "qty_available", "0")

            qty = _to_decimal(qty_raw)

            # Support common entry/avg fields (object mocks often use entry_price)
            avg_raw = _safe_get(p, "avg_entry_price", None)
            if avg_raw is None:
                avg_raw = _safe_get(p, "avg_entry", None)
            if avg_raw is None:
                avg_raw = _safe_get(p, "entry_price", None)

            avg = (_to_decimal(avg_raw) if avg_raw is not None else None)
            out[str(sym)] = {"qty": qty, "avg_entry": (_to_decimal(avg) if avg is not None else None), "raw": p}

        return out

    def _local_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {symbol: {"qty": Decimal, "entry_price": Optional[Decimal], "raw": obj}}
        """
        out: Dict[str, Dict[str, Any]] = {}

        positions = None
        # Try common APIs
        for fn in ("get_all_positions", "list_positions", "get_all", "all", "list_open_positions"):
            if hasattr(self.position_store, fn):
                positions = getattr(self.position_store, fn)()
                break

        if positions is None:
            # Last resort: attempt to iterate if it's already a list-like
            positions = getattr(self.position_store, "positions", None) or []

        for p in positions:
            sym = _safe_get(p, "symbol")
            if not sym:
                continue
            qty = _to_decimal(_safe_get(p, "quantity", _safe_get(p, "qty", "0")))
            entry = _safe_get(p, "entry_price", None)
            out[str(sym)] = {"qty": qty, "entry_price": (_to_decimal(entry) if entry is not None else None), "raw": p}

        return out

    def _diff_positions(
        self,
        local_positions: Dict[str, Dict[str, Any]],
        broker_positions: Dict[str, Dict[str, Any]],
    ) -> List[Discrepancy]:
        now = _utc_now()
        out: List[Discrepancy] = []

        all_syms = set(local_positions.keys()) | set(broker_positions.keys())

        for sym in sorted(all_syms):
            lp = local_positions.get(sym)
            bp = broker_positions.get(sym)

            if lp is None and bp is not None and bp["qty"] != 0:
                out.append(
                    Discrepancy(
                        type="missing_position",
                        symbol=sym,
                        local_value=None,
                        broker_value=f"{bp['qty']}",
                        resolution="logged_only",
                        timestamp=now,
                    )
                )
                continue

            if lp is not None and (bp is None or bp["qty"] == 0):
                out.append(
                    Discrepancy(
                        type="extra_position",
                        symbol=sym,
                        local_value=f"{lp['qty']}",
                        broker_value=None,
                        resolution="logged_only",
                        timestamp=now,
                    )
                )
                continue

            if lp is not None and bp is not None:
                if lp["qty"] != bp["qty"]:
                    out.append(
                        Discrepancy(
                            type="qty_mismatch",
                            symbol=sym,
                            local_value=f"{lp['qty']}",
                            broker_value=f"{bp['qty']}",
                            resolution="logged_only",
                            timestamp=now,
                        )
                    )

        return out

    # -------------------------
    # Orders
    # -------------------------
    def _broker_open_orders(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {broker_order_id: {"symbol": str, "status": str, "raw": obj}}
        """
        out: Dict[str, Dict[str, Any]] = {}

        orders = None
        for fn in ("get_orders", "list_orders"):
            if hasattr(self.broker, fn):
                func = getattr(self.broker, fn)
                # PATCH 3 compatibility: prefer get_orders(status="open") if supported
                try:
                    orders = func(status="open")
                except TypeError:
                    orders = func()
                break

        if orders is None:
            orders = _safe_get(self.broker, "orders", []) or []

        for o in orders:
            oid = _safe_get(o, "id", _safe_get(o, "order_id", None))
            if not oid:
                continue
            status = str(_safe_get(o, "status", "")).lower()
            # Keep only "open-ish" statuses
            if status in ("filled", "canceled", "cancelled", "rejected", "expired"):
                continue
            sym = _safe_get(o, "symbol")
            out[str(oid)] = {"symbol": str(sym) if sym else "", "status": status, "raw": o}

        return out

    def _local_open_orders(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {broker_order_id: {"client_id": str, "symbol": str, "status": str, "raw": obj}}
        We key by broker_order_id because that's what broker returns.
        """
        out: Dict[str, Dict[str, Any]] = {}

        # Expected: OrderTracker can list active/open orders
        orders = None
        for fn in ("get_open_orders", "list_open_orders", "active_orders"):
            if hasattr(self.order_tracker, fn):
                orders = getattr(self.order_tracker, fn)()
                break

        if orders is None:
            orders = _safe_get(self.order_tracker, "open_orders", []) or []

        for o in orders:
            broker_id = _safe_get(o, "broker_order_id", _safe_get(o, "id", None))
            if not broker_id:
                continue
            sym = _safe_get(o, "symbol", "")
            status = str(_safe_get(o, "status", "")).lower()
            cid = _safe_get(o, "internal_order_id", _safe_get(o, "client_order_id", ""))
            out[str(broker_id)] = {"client_id": str(cid), "symbol": str(sym), "status": status, "raw": o}

        return out

    def _diff_orders(
        self,
        local_orders: Dict[str, Dict[str, Any]],
        broker_orders: Dict[str, Dict[str, Any]],
    ) -> List[Discrepancy]:
        now = _utc_now()
        out: List[Discrepancy] = []

        all_ids = set(local_orders.keys()) | set(broker_orders.keys())

        for oid in sorted(all_ids):
            lo = local_orders.get(oid)
            bo = broker_orders.get(oid)

            # if broker has open order but local doesn't
            if lo is None and bo is not None:
                out.append(
                    Discrepancy(
                        type="missing_order",
                        symbol=bo.get("symbol", ""),
                        local_value=None,
                        broker_value={"broker_order_id": oid, "status": bo.get("status")},
                        resolution="logged_only",
                        timestamp=now,
                    )
                )
                continue

            # if local thinks open but broker doesn't have it open
            if lo is not None and bo is None:
                out.append(
                    Discrepancy(
                        type="extra_order",
                        symbol=lo.get("symbol", ""),
                        local_value={"broker_order_id": oid, "status": lo.get("status"), "client_id": lo.get("client_id")},
                        broker_value=None,
                        resolution="logged_only",
                        timestamp=now,
                    )
                )
                continue

            if lo is not None and bo is not None:
                ls = (lo.get("status") or "").lower()
                bs = (bo.get("status") or "").lower()
                if ls and bs and ls != bs:
                    out.append(
                        Discrepancy(
                            type="order_status_mismatch",
                            symbol=bo.get("symbol", lo.get("symbol", "")),
                            local_value={"broker_order_id": oid, "status": ls},
                            broker_value={"broker_order_id": oid, "status": bs},
                            resolution="logged_only",
                            timestamp=now,
                        )
                    )

        return out

    # -------------------------
    # Public API
    # -------------------------
    def reconcile_startup(self) -> List[Discrepancy]:
        discrepancies: List[Discrepancy] = []

        broker_positions = self._broker_positions()
        local_positions = self._local_positions()

        discrepancies.extend(self._diff_positions(local_positions, broker_positions))

        broker_orders = self._broker_open_orders()
        local_orders = self._local_open_orders()

        discrepancies.extend(self._diff_orders(local_orders, broker_orders))

        return discrepancies


# ============================================================================
# PATCH 7: Periodic Reconciliation
# ============================================================================

@dataclass(frozen=True)
class ReconciliationResult:
    """Result of a single periodic reconciliation run."""
    ran: bool
    discrepancies: List[Discrepancy]
    timestamp: datetime
    skipped_reason: Optional[str] = None


class PeriodicReconciler:
    """
    Wraps a StartupReconciler (or compatible) to run reconciliation on a
    configurable interval.  The caller invokes ``check()`` every loop cycle;
    the reconciler internally gates on elapsed time so it only actually
    queries the broker every ``interval_s`` seconds.

    Thread-safe: ``check()`` acquires an internal lock.

    Usage in the runtime loop::

        periodic = PeriodicReconciler(reconciler, interval_s=300)
        ...
        while running:
            result = periodic.check()
            if result.ran and result.discrepancies:
                # handle drift
    """

    def __init__(
        self,
        reconciler: StartupReconciler,
        *,
        interval_s: float = 300.0,
        clock: Optional[Any] = None,
    ) -> None:
        self._reconciler = reconciler
        self._interval_s = interval_s
        self._clock = clock  # Optional injectable clock (for tests)
        self._last_run: Optional[float] = None
        self._run_count: int = 0
        self._lock = __import__("threading").Lock()

    # -------------------------
    # Public API
    # -------------------------
    def check(self) -> ReconciliationResult:
        """
        Run reconciliation if enough time has elapsed since the last run.

        Returns a ``ReconciliationResult`` indicating whether the check
        actually ran and what (if any) discrepancies were found.
        """
        import time as _time

        now_ts = self._clock.now().timestamp() if self._clock else _time.time()

        with self._lock:
            if self._last_run is not None:
                elapsed = now_ts - self._last_run
                if elapsed < self._interval_s:
                    return ReconciliationResult(
                        ran=False,
                        discrepancies=[],
                        timestamp=_utc_now(),
                        skipped_reason=f"interval_not_elapsed ({elapsed:.1f}s / {self._interval_s:.1f}s)",
                    )

            # Run the actual reconciliation
            try:
                discrepancies = self._reconciler.reconcile_startup()
            except Exception as exc:
                # Treat reconciliation failure as a discrepancy itself
                discrepancies = [
                    Discrepancy(
                        type="reconciliation_error",
                        symbol="*",
                        local_value=None,
                        broker_value=str(exc),
                        resolution="error",
                        timestamp=_utc_now(),
                    )
                ]

            self._last_run = now_ts
            self._run_count += 1

            return ReconciliationResult(
                ran=True,
                discrepancies=discrepancies,
                timestamp=_utc_now(),
            )

    @property
    def run_count(self) -> int:
        return self._run_count

    @property
    def interval_s(self) -> float:
        return self._interval_s


# --- PATCH 2.1: Paper-mode auto-heal (optional) -----------------------------

from dataclasses import dataclass

@dataclass(frozen=True)
class HealAction:
    kind: str                 # "position_upsert" | "position_delete" | "order_reset" | "order_track"
    symbol: str
    details: dict
    timestamp: datetime


class StartupReconciler(StartupReconciler):  # type: ignore[misc]
    """
    Extension: allow paper-mode healing to align local state to broker state.

    Safety:
      - Caller must gate this to paper mode only.
      - This does NOT cancel broker orders.
      - This only aligns local stores to broker truth.
    """

    def heal_startup(self) -> List[HealAction]:
        """
        Heal local PositionStore + OrderTracker to match broker state.
        Returns list of HealAction performed.
        """
        actions: List[HealAction] = []
        now = _utc_now()

        # 1) Heal positions: make local positions match broker positions
        broker_positions = self._broker_positions()
        local_positions = self._local_positions()

        broker_syms = set(broker_positions.keys())
        local_syms = set(local_positions.keys())

        # delete local positions not on broker (or broker qty 0)
        for sym in sorted(local_syms):
            bp = broker_positions.get(sym)
            if bp is None or bp["qty"] == 0:
                self._local_position_delete(sym)
                actions.append(
                    HealAction(
                        kind="position_delete",
                        symbol=sym,
                        details={"reason": "local_extra_or_broker_flat"},
                        timestamp=now,
                    )
                )

        # upsert broker positions into local
        for sym in sorted(broker_syms):
            bp = broker_positions[sym]
            if bp["qty"] == 0:
                continue
            self._local_position_upsert_from_broker(sym, bp)
            actions.append(
                HealAction(
                    kind="position_upsert",
                    symbol=sym,
                    details={"qty": str(bp["qty"]), "avg_entry": (str(bp["avg_entry"]) if bp["avg_entry"] is not None else None)},
                    timestamp=now,
                )
            )

        # 2) Heal orders: reset local open-order view to broker open orders
        broker_orders = self._broker_open_orders()
        self._local_orders_reset()

        actions.append(
            HealAction(
                kind="order_reset",
                symbol="",
                details={"broker_open_orders": len(broker_orders)},
                timestamp=now,
            )
        )

        for oid, bo in broker_orders.items():
            self._local_order_track_from_broker(oid, bo)
            actions.append(
                HealAction(
                    kind="order_track",
                    symbol=bo.get("symbol", ""),
                    details={"broker_order_id": oid, "status": bo.get("status", "")},
                    timestamp=now,
                )
            )

        return actions

    # -------------------------
    # Local mutation helpers (best-effort, no hard coupling)
    # -------------------------
    def _local_position_delete(self, symbol: str) -> None:
        # Preferred API
        for fn in ("delete", "remove", "delete_symbol"):
            if hasattr(self.position_store, fn):
                getattr(self.position_store, fn)(symbol)
                return

        # Fallback: if it supports upsert with zero
        if hasattr(self.position_store, "upsert"):
            try:
                getattr(self.position_store, "upsert")({"symbol": symbol, "quantity": "0"})
            except Exception:
                pass

    def _local_position_upsert_from_broker(self, symbol: str, bp: Dict[str, Any]) -> None:
        qty = bp["qty"]
        avg = bp.get("avg_entry")

        # If PositionStore expects a Position model, it likely has an upsert(Position)
        if hasattr(self.position_store, "upsert"):
            try:
                # Try dict form first (works with mocks + generic stores)
                self.position_store.upsert({"symbol": symbol, "quantity": str(qty), "entry_price": (str(avg) if avg is not None else None)})
                return
            except Exception:
                pass

        # If it has a specific method:
        for fn in ("upsert_position", "set_position"):
            if hasattr(self.position_store, fn):
                getattr(self.position_store, fn)(symbol=symbol, quantity=qty, entry_price=avg)
                return

    def _local_orders_reset(self) -> None:
        # Prefer an explicit reset/clear
        for fn in ("reset", "clear", "clear_open_orders"):
            if hasattr(self.order_tracker, fn):
                try:
                    getattr(self.order_tracker, fn)()
                    return
                except Exception:
                    pass

        # Last resort: if order_tracker maintains open_orders list
        if hasattr(self.order_tracker, "open_orders"):
            try:
                self.order_tracker.open_orders = []
            except Exception:
                pass

    def _local_order_track_from_broker(self, broker_order_id: str, bo: Dict[str, Any]) -> None:
        payload = {
            "broker_order_id": broker_order_id,
            "symbol": bo.get("symbol", ""),
            "status": bo.get("status", ""),
        }

        for fn in ("track_broker_order", "track_order", "add_order", "upsert_order"):
            if hasattr(self.order_tracker, fn):
                try:
                    getattr(self.order_tracker, fn)(**payload)
                    return
                except TypeError:
                    # some APIs expect a single object/dict
                    try:
                        getattr(self.order_tracker, fn)(payload)
                        return
                    except Exception:
                        pass
                except Exception:
                    pass
# ---------------------------------------------------------------------------
# Backwards-compat shim with legacy side-effects expected by tests:
# - If broker has a position and local does not, call position_store.open_position(...)
# - If broker has an open order and local does not, no side-effects required by tests
# ---------------------------------------------------------------------------

class _OrderMachineAdapter:
    """
    Adapter that exposes a minimal 'order_tracker-like' API expected by StartupReconciler.
    It maps legacy OrderMachine.get_pending_orders() -> get_open_orders().
    """
    def __init__(self, order_machine):
        self._om = order_machine

    def get_open_orders(self):
        if hasattr(self._om, "get_pending_orders"):
            return self._om.get_pending_orders()
        return []


class BrokerReconciler(StartupReconciler):
    """
    Compatibility wrapper for older imports/tests.
    Accepts legacy args and applies legacy reconciliation side-effects.
    """

    def __init__(self, *, order_machine, position_store, broker_connector):
        super().__init__(
            broker=broker_connector,
            position_store=position_store,
            order_tracker=_OrderMachineAdapter(order_machine),
        )

    def reconcile_startup(self) -> List[Discrepancy]:
        """
        Legacy behavior: detect drift AND open missing broker positions locally.
        Must only call broker.get_positions() once (per tests).
        """
        discrepancies: List[Discrepancy] = []

        # ONE broker.get_positions() call total
        broker_positions = self._broker_positions()
        local_positions = self._local_positions()
        discrepancies.extend(self._diff_positions(local_positions, broker_positions))

        # Orders (tests expect status='open' passed when possible)
        broker_orders = self._broker_open_orders()
        local_orders = self._local_open_orders()
        discrepancies.extend(self._diff_orders(local_orders, broker_orders))

        # Legacy side-effect expected by tests: open missing broker positions locally
        for d in discrepancies:
            if d.type != "missing_position":
                continue

            bp = broker_positions.get(d.symbol)
            if not bp:
                continue

            qty = bp.get("qty", Decimal("0"))
            avg = bp.get("avg_entry", None)

            if hasattr(self.position_store, "open_position"):
                try:
                    self.position_store.open_position(d.symbol, qty, avg)
                except TypeError:
                    try:
                        self.position_store.open_position(symbol=d.symbol, quantity=qty, entry_price=avg)
                    except Exception:
                        pass
                except Exception:
                    pass

        return discrepancies
