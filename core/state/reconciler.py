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
    return getattr(obj, key, default)


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

    # -------------------------
    # Positions
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
            qty = _to_decimal(_safe_get(p, "qty", _safe_get(p, "quantity", "0")))
            avg = _safe_get(p, "avg_entry_price", _safe_get(p, "avg_entry", None))
            out[str(sym)] = {"qty": qty, "avg_entry": (_to_decimal(avg) if avg is not None else None), "raw": p}

        return out

    def _local_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns {symbol: {"qty": Decimal, "entry_price": Optional[Decimal], "raw": obj}}
        """
        out: Dict[str, Dict[str, Any]] = {}

        positions = None
        # Try common APIs
        for fn in ("list_positions", "get_all", "all", "list_open_positions"):
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
                try:
                    orders = getattr(self.broker, fn)()
                except TypeError:
                    # some brokers require params; fall back to no-arg best effort
                    orders = getattr(self.broker, fn)()
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
                        type="order_missing_local",
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
                        type="order_missing_broker",
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
