"""
Order execution engine - bridges state machine and broker.

CRITICAL PROPERTIES:
1. Synchronous order submission
2. Status polling with timeout
3. State machine integration
4. Position store updates on fill
5. Order ID mapping (internal <-> broker)
6. Fill detection and notification

PATCH 1 (2026-02-14):
- Wire stop_loss / take_profit into REAL protective orders at the broker.
- Implement synthetic OCO behavior: if SL fills, cancel TP; if TP fills, cancel SL.
- Deterministic protective internal IDs: "{entry_id}::SL" and "{entry_id}::TP".
- Ensure limit/stop submissions also populate _internal_to_broker_id and are idempotent.

Based on LEAN's OrderTicket pattern.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from core.brokers.alpaca_connector import (
    AlpacaBrokerConnector,
    BrokerOrderError,
    BrokerOrderSide,
)
from core.journal.trade_journal import TradeIds, TradeJournal, build_trade_event
from core.logging import LogContext, LogStream, get_logger
from core.market.symbol_properties import SymbolPropertiesCache
from core.state import (
    OrderStateMachine,
    OrderStatus,
    Position,
    PositionStore,
)
from core.state.order_tracker import (
    FillEvent,
    InFlightOrder,
    OrderSide as TrackerOrderSide,
    OrderTracker,
    OrderType,
)

# ============================================================================


class OrderExecutionEngine:
    """
    Order execution engine.

    RESPONSIBILITIES:
    - Submit orders to broker
    - Poll order status
    - Transition state machine on status updates
    - Update position store on fills
    - Emit events

    THREAD SAFETY:
    - Thread-safe via internal locks
    - Can be called from multiple threads
    """

    # Deterministic suffixes for protective orders
    _SL_SUFFIX = "::SL"
    _TP_SUFFIX = "::TP"

    def __init__(
        self,
        broker: AlpacaBrokerConnector,
        state_machine: OrderStateMachine,
        position_store: PositionStore,
        symbol_properties: Optional[SymbolPropertiesCache] = None,
        order_tracker: Optional[OrderTracker] = None,
        transaction_log=None,  # optional TransactionLog for crash-restart seeding
    ):
        self.broker = broker
        self.state_machine = state_machine
        self.position_store = position_store
        self.symbol_properties = symbol_properties
        self.order_tracker = order_tracker
        self.logger = get_logger(LogStream.ORDERS)

        self.trade_journal: Optional[TradeJournal] = None
        self._run_id: str = TradeJournal.new_run_id()
        self._trade_ids_by_internal: Dict[str, str] = {}

        self.transaction_log = transaction_log
        self._internal_to_broker_id: dict[str, str] = {}

        # Track order metadata
        self._order_metadata: Dict[str, Dict[str, Any]] = {}
        self._metadata_lock = threading.Lock()

        # Duplicate order prevention (engine-level defense)
        self._submitted_order_ids: set[str] = set()

        # PATCH 3: Track cumulative filled quantity for partial fills
        self._cumulative_filled_qty: Dict[str, Decimal] = {}

        # Seed duplicate-order guard from persistent transaction log after restart.
        if transaction_log is not None:
            self._seed_submitted_ids_from_log(transaction_log)

        self.logger.info("OrderExecutionEngine initialized")

    # ---------------------------------------------------------------------
    # JOURNAL / TX LOG HELPERS
    # ---------------------------------------------------------------------

    def _seed_submitted_ids_from_log(self, transaction_log) -> None:
        """Replay ORDER_SUBMIT events from the transaction log to rebuild
        the in-memory duplicate-order guard after a restart."""
        try:
            for event in transaction_log.iter_events():
                et = event.get("event_type") or event.get("event")
                if str(et).upper() == "ORDER_SUBMIT":
                    oid = event.get("internal_order_id")
                    if oid:
                        self._submitted_order_ids.add(oid)
            if self._submitted_order_ids:
                self.logger.info(
                    "Seeded %d submitted order IDs from transaction log",
                    len(self._submitted_order_ids),
                )
        except Exception:
            self.logger.warning(
                "Failed to seed submitted IDs from transaction log",
                exc_info=True,
            )

    def set_trade_journal(self, journal: TradeJournal, run_id: Optional[str] = None) -> None:
        self.trade_journal = journal
        if run_id:
            self._run_id = run_id

    def register_trade_id(self, internal_order_id: str, trade_id: str) -> None:
        """Pre-register a trade_id for an internal_order_id so that journal
        events use the same trade_id as the caller (e.g. runtime signal)."""
        self._trade_ids_by_internal[internal_order_id] = trade_id

    def _trade_ids(self, internal_order_id: str) -> TradeIds:
        tid = self._trade_ids_by_internal.get(internal_order_id)
        if not tid:
            tid = TradeJournal.new_trade_id()
            self._trade_ids_by_internal[internal_order_id] = tid
        return TradeIds(run_id=self._run_id, trade_id=tid)

    def _j_emit(self, event: Dict[str, Any]) -> None:
        if self.trade_journal is None:
            return
        try:
            self.trade_journal.emit(event)
        except Exception:
            # journal must never kill execution
            pass

    # ---------------------------------------------------------------------
    # PATCH 1: PROTECTIVE ORDER HELPERS
    # ---------------------------------------------------------------------

    def _sl_id(self, entry_internal_order_id: str) -> str:
        return f"{entry_internal_order_id}{self._SL_SUFFIX}"

    def _tp_id(self, entry_internal_order_id: str) -> str:
        return f"{entry_internal_order_id}{self._TP_SUFFIX}"

    def _is_protective_internal_id(self, internal_order_id: str) -> bool:
        return internal_order_id.endswith(self._SL_SUFFIX) or internal_order_id.endswith(self._TP_SUFFIX)

    def _link_oco(self, a_internal: str, b_internal: str) -> None:
        """Record sibling linkage in metadata for OCO cancellation."""
        with self._metadata_lock:
            a = self._order_metadata.get(a_internal) or {}
            b = self._order_metadata.get(b_internal) or {}
            a["oco_sibling_internal_id"] = b_internal
            b["oco_sibling_internal_id"] = a_internal
            self._order_metadata[a_internal] = a
            self._order_metadata[b_internal] = b

    def _maybe_cancel_oco_sibling_on_fill(self, internal_order_id: str, reason: str) -> None:
        """If this order is part of an OCO pair, cancel the sibling when this fills."""
        with self._metadata_lock:
            meta = self._order_metadata.get(internal_order_id) or {}
            sib_internal = meta.get("oco_sibling_internal_id")

        if not sib_internal:
            return

        sib_broker = self._internal_to_broker_id.get(sib_internal)
        if not sib_broker:
            # best effort: might not be submitted yet or mapping lost
            self.logger.warning(
                "OCO sibling missing broker id mapping; cannot cancel sibling",
                extra={"internal_order_id": internal_order_id, "sibling_internal": sib_internal},
            )
            return

        self.logger.info(
            "OCO: cancelling sibling order because peer filled",
            extra={"filled_internal": internal_order_id, "sibling_internal": sib_internal, "sibling_broker": sib_broker},
        )
        self.cancel_order(
            internal_order_id=sib_internal,
            broker_order_id=sib_broker,
            reason=reason,
        )

    def _ensure_protective_orders_for_entry(
        self,
        entry_internal_order_id: str,
        symbol: str,
        filled_qty: Decimal,
        entry_fill_price: Decimal,
        strategy: str,
        stop_loss: Optional[Decimal],
        take_profit: Optional[Decimal],
    ) -> None:
        """
        PATCH 1: Submit synthetic protective orders for an entry.

        For a long entry:
          - stop_loss => submit STOP SELL (market stop) at stop_loss
          - take_profit => submit LIMIT SELL at take_profit

        Orders are linked as OCO. Deterministic internal IDs prevent duplicates.

        NOTE: We validate basic directional sanity. If invalid, we log and skip that leg.
        """
        if filled_qty is None or filled_qty <= 0:
            return
        if stop_loss is None and take_profit is None:
            return

        # Only create protection for entry orders (BUY fills), not for protective exits.
        if self._is_protective_internal_id(entry_internal_order_id):
            return

        sl_internal = self._sl_id(entry_internal_order_id)
        tp_internal = self._tp_id(entry_internal_order_id)

        created_any = False
        created_sl = False
        created_tp = False

        # STOP LOSS leg
        if stop_loss is not None:
            try:
                # Long: stop_loss should be below fill price (otherwise instant trigger / nonsense)
                if stop_loss >= entry_fill_price:
                    self.logger.error(
                        "Invalid stop_loss for long entry; skipping SL leg",
                        extra={
                            "symbol": symbol,
                            "entry_internal_order_id": entry_internal_order_id,
                            "stop_loss": str(stop_loss),
                            "fill_price": str(entry_fill_price),
                        },
                    )
                else:
                    # idempotent submit_stop_order will return existing mapping if already submitted
                    sl_broker_id = self.submit_stop_order(
                        internal_order_id=sl_internal,
                        symbol=symbol,
                        quantity=filled_qty,
                        side=BrokerOrderSide.SELL,
                        stop_price=stop_loss,
                        strategy=f"{strategy}::protective",
                    )
                    created_any = True
                    created_sl = True

                    with self._metadata_lock:
                        m = self._order_metadata.get(sl_internal) or {}
                        m.update(
                            {
                                "parent_internal_order_id": entry_internal_order_id,
                                "protective_kind": "STOP_LOSS",
                                "symbol": symbol,
                                "strategy": f"{strategy}::protective",
                            }
                        )
                        self._order_metadata[sl_internal] = m

                    self.logger.info(
                        "Protective SL submitted",
                        extra={
                            "symbol": symbol,
                            "entry_internal_order_id": entry_internal_order_id,
                            "sl_internal_order_id": sl_internal,
                            "sl_broker_order_id": sl_broker_id,
                            "stop_loss": str(stop_loss),
                            "qty": str(filled_qty),
                        },
                    )
            except Exception:
                self.logger.error(
                    "Failed to submit protective SL",
                    extra={"symbol": symbol, "entry_internal_order_id": entry_internal_order_id},
                    exc_info=True,
                )

        # TAKE PROFIT leg
        if take_profit is not None:
            try:
                # Long: take_profit should be above fill price
                if take_profit <= entry_fill_price:
                    self.logger.error(
                        "Invalid take_profit for long entry; skipping TP leg",
                        extra={
                            "symbol": symbol,
                            "entry_internal_order_id": entry_internal_order_id,
                            "take_profit": str(take_profit),
                            "fill_price": str(entry_fill_price),
                        },
                    )
                else:
                    tp_broker_id = self.submit_limit_order(
                        internal_order_id=tp_internal,
                        symbol=symbol,
                        quantity=filled_qty,
                        side=BrokerOrderSide.SELL,
                        limit_price=take_profit,
                        strategy=f"{strategy}::protective",
                        stop_loss=None,
                        take_profit=None,
                    )
                    created_any = True
                    created_tp = True

                    with self._metadata_lock:
                        m = self._order_metadata.get(tp_internal) or {}
                        m.update(
                            {
                                "parent_internal_order_id": entry_internal_order_id,
                                "protective_kind": "TAKE_PROFIT",
                                "symbol": symbol,
                                "strategy": f"{strategy}::protective",
                            }
                        )
                        self._order_metadata[tp_internal] = m

                    self.logger.info(
                        "Protective TP submitted",
                        extra={
                            "symbol": symbol,
                            "entry_internal_order_id": entry_internal_order_id,
                            "tp_internal_order_id": tp_internal,
                            "tp_broker_order_id": tp_broker_id,
                            "take_profit": str(take_profit),
                            "qty": str(filled_qty),
                        },
                    )
            except Exception:
                self.logger.error(
                    "Failed to submit protective TP",
                    extra={"symbol": symbol, "entry_internal_order_id": entry_internal_order_id},
                    exc_info=True,
                )

        # Link OCO only if we have both legs actually submitted
        if created_sl and created_tp:
            self._link_oco(sl_internal, tp_internal)

        # Also store on the entry metadata for traceability
        if created_any:
            with self._metadata_lock:
                em = self._order_metadata.get(entry_internal_order_id) or {}
                em["protective_sl_internal_id"] = sl_internal if created_sl else None
                em["protective_tp_internal_id"] = tp_internal if created_tp else None
                self._order_metadata[entry_internal_order_id] = em

    def _cancel_protective_orders_for_entry(self, entry_internal_order_id: str, reason: str) -> None:
        """
        Cancel any outstanding protective orders (SL/TP) for a given entry.

        This is required for synthetic brackets:
        - If we manually exit a position, we must cancel the leftover protective legs
          or they can re-open/flip exposure later.
        """
        if not entry_internal_order_id:
            return

        sl_internal = self._sl_id(entry_internal_order_id)
        tp_internal = self._tp_id(entry_internal_order_id)

        for child_internal in (sl_internal, tp_internal):
            child_broker = self._internal_to_broker_id.get(child_internal)
            if not child_broker:
                continue

            self.logger.info(
                "Cancelling protective child order because position closed",
                extra={
                    "entry_internal_order_id": entry_internal_order_id,
                    "child_internal_order_id": child_internal,
                    "child_broker_order_id": child_broker,
                    "reason": reason,
                },
            )
            self.cancel_order(
                internal_order_id=child_internal,
                broker_order_id=child_broker,
                reason=reason,
            )

    # ---------------------------------------------------------------------
    # ORDER SUBMISSION
    # ---------------------------------------------------------------------

    def submit_market_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        """Submit market order to broker. Returns broker_order_id."""
        with LogContext(internal_order_id):
            # Idempotency: if we already submitted this internal id, return the same broker id.
            if internal_order_id in self._submitted_order_ids:
                existing = self._internal_to_broker_id.get(internal_order_id)
                if existing:
                    self.logger.warning(
                        "Idempotent submit_market_order: duplicate internal_order_id; returning existing broker_order_id",
                        extra={"internal_order_id": internal_order_id, "broker_order_id": existing},
                    )
                    return existing
                error_msg = (
                    f"DUPLICATE_ORDER: {internal_order_id} already submitted to broker "
                    "(missing broker id mapping)"
                )
                self.logger.error(error_msg)
                raise DuplicateOrderError(error_msg)

            try:
                # Validate and round order using symbol properties (if available)
                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=None,
                            side=side.value,
                        )
                        if not is_valid:
                            raise OrderValidationError(f"Order validation failed: {reason}")

                        # Round quantity to lot size
                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                "Quantity rounded: %s -> %s (lot_size=%s)",
                                quantity,
                                rounded_qty,
                                props.lot_size,
                            )
                            quantity = Decimal(str(rounded_qty))

                # Store metadata
                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "submitted_at": datetime.now(timezone.utc),
                    }

                broker_order_id = self.broker.submit_market_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    internal_order_id=internal_order_id,
                )

                self._submitted_order_ids.add(internal_order_id)
                self._internal_to_broker_id[internal_order_id] = broker_order_id

                # journal ORDER_SUBMIT (market)
                ids = self._trade_ids(internal_order_id)
                self._j_emit(
                    build_trade_event(
                        event_type="ORDER_SUBMIT",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        symbol=symbol,
                        side=side.value,
                        qty=str(quantity),
                        order_type="MARKET",
                        strategy=strategy,
                        reason={"source": "engine"},
                    )
                )

                # durable transaction log: submit + ack
                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_SUBMIT",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "side": side.value,
                                "qty": str(quantity),
                                "order_type": "MARKET",
                                "strategy": strategy,
                            }
                        )
                        self.transaction_log.append(
                            {
                                "event_type": "BROKER_ORDER_ACK",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "ack": True,
                            }
                        )
                    except Exception:
                        pass

                # Track order in OrderTracker
                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                        client_order_id=internal_order_id,
                        exchange_order_id=broker_order_id,
                        symbol=symbol,
                        quantity=quantity,
                        side=tracker_side,
                        order_type=OrderType.MARKET,
                        price=None,
                        strategy_id=strategy,
                        submitted_at=datetime.now(timezone.utc),
                    )
                    self.order_tracker.start_tracking(in_flight_order)

                # Transition: PENDING -> SUBMITTED
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id,
                )

                return broker_order_id

            except OrderValidationError:
                raise
            except Exception as e:
                self.logger.error(
                    "Order submission failed",
                    extra={"internal_order_id": internal_order_id, "symbol": symbol, "error": str(e)},
                    exc_info=True,
                )
                ids = self._trade_ids(internal_order_id)
                self._j_emit(
                    build_trade_event(
                        event_type="ERROR",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        symbol=symbol,
                        error={"where": "OrderExecutionEngine", "message": str(e)},
                    )
                )

                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ERROR",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": locals().get("broker_order_id"),
                                "symbol": symbol,
                                "error": str(e),
                            }
                        )
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_REJECTED",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": locals().get("broker_order_id"),
                                "symbol": symbol,
                                "reason": str(e),
                            }
                        )
                    except Exception:
                        pass

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e),
                )
                raise OrderExecutionError(f"Failed to submit order: {e}") from e

    def submit_limit_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        limit_price: Decimal,
        strategy: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> str:
        """
        Submit limit order to broker.

        IMPORTANT: Designed for "one attempt only" entries:
        - Caller should wait/poll for fill up to TTL
        - If not filled, cancel (do NOT reprice/chase)

        Returns broker_order_id.
        """
        with LogContext(internal_order_id):
            # Idempotency like market orders
            if internal_order_id in self._submitted_order_ids:
                existing = self._internal_to_broker_id.get(internal_order_id)
                if existing:
                    self.logger.warning(
                        "Idempotent submit_limit_order: duplicate internal_order_id; returning existing broker_order_id",
                        extra={"internal_order_id": internal_order_id, "broker_order_id": existing},
                    )
                    return existing
                raise DuplicateOrderError(f"DUPLICATE_ORDER: {internal_order_id} already submitted (missing mapping)")

            try:
                if limit_price is None or limit_price <= 0:
                    raise OrderValidationError(f"limit_price must be positive, got {limit_price}")

                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=float(limit_price),
                            side=side.value,
                        )
                        if not is_valid:
                            raise OrderValidationError(f"Order validation failed: {reason}")

                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                "Quantity rounded: %s -> %s (lot_size=%s)",
                                quantity,
                                rounded_qty,
                                props.lot_size,
                            )
                            quantity = Decimal(str(rounded_qty))

                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "limit_price": limit_price,
                        "submitted_at": datetime.now(timezone.utc),
                    }

                broker_order_id = self.broker.submit_limit_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    limit_price=limit_price,
                    internal_order_id=internal_order_id,
                )

                self._submitted_order_ids.add(internal_order_id)
                self._internal_to_broker_id[internal_order_id] = broker_order_id

                ids = self._trade_ids(internal_order_id)
                self._j_emit(
                    build_trade_event(
                        event_type="ORDER_SUBMIT",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        symbol=symbol,
                        side=side.value,
                        qty=str(quantity),
                        order_type="LIMIT",
                        limit_price=str(limit_price),
                        strategy=strategy,
                        reason={"source": "engine"},
                    )
                )

                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_SUBMIT",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "side": side.value,
                                "qty": str(quantity),
                                "order_type": "LIMIT",
                                "limit_price": str(limit_price),
                                "strategy": strategy,
                            }
                        )
                        self.transaction_log.append(
                            {
                                "event_type": "BROKER_ORDER_ACK",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "ack": True,
                            }
                        )
                    except Exception:
                        pass

                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                        client_order_id=internal_order_id,
                        exchange_order_id=broker_order_id,
                        symbol=symbol,
                        quantity=quantity,
                        side=tracker_side,
                        order_type=OrderType.LIMIT,
                        price=limit_price,
                        strategy_id=strategy,
                        submitted_at=datetime.now(timezone.utc),
                    )
                    self.order_tracker.start_tracking(in_flight_order)

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id,
                )
                return broker_order_id

            except OrderValidationError:
                raise
            except Exception as e:
                self.logger.error(
                    "Limit order submission failed",
                    extra={"internal_order_id": internal_order_id, "symbol": symbol, "error": str(e)},
                    exc_info=True,
                )
                ids = self._trade_ids(internal_order_id)
                self._j_emit(
                    build_trade_event(
                        event_type="ERROR",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        symbol=symbol,
                        error={"where": "OrderExecutionEngine", "message": str(e)},
                    )
                )

                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ERROR",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": locals().get("broker_order_id"),
                                "symbol": symbol,
                                "error": str(e),
                            }
                        )
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_REJECTED",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": locals().get("broker_order_id"),
                                "symbol": symbol,
                                "reason": str(e),
                            }
                        )
                    except Exception:
                        pass

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e),
                )
                raise OrderExecutionError(f"Failed to submit limit order: {e}") from e

    def submit_stop_order(
        self,
        internal_order_id: str,
        symbol: str,
        quantity: Decimal,
        side: BrokerOrderSide,
        stop_price: Decimal,
        strategy: str,
    ) -> str:
        """
        Submit stop (market) order to broker.

        PATCH 1: This is now idempotent and records _internal_to_broker_id mapping.
        """
        with LogContext(internal_order_id):
            if internal_order_id in self._submitted_order_ids:
                existing = self._internal_to_broker_id.get(internal_order_id)
                if existing:
                    self.logger.warning(
                        "Idempotent submit_stop_order: duplicate internal_order_id; returning existing broker_order_id",
                        extra={"internal_order_id": internal_order_id, "broker_order_id": existing},
                    )
                    return existing
                raise DuplicateOrderError(f"DUPLICATE_ORDER: {internal_order_id} already submitted (missing mapping)")

            try:
                if stop_price is None or stop_price <= 0:
                    raise OrderValidationError(f"stop_price must be positive, got {stop_price}")

                if self.symbol_properties:
                    props = self.symbol_properties.get(symbol)
                    if props:
                        is_valid, reason = props.validate_order(
                            quantity=int(quantity),
                            price=float(stop_price),
                            side=side.value,
                        )
                        if not is_valid:
                            raise OrderValidationError(f"Stop order validation failed: {reason}")

                        rounded_qty = props.round_quantity(int(quantity))
                        if rounded_qty != int(quantity):
                            self.logger.warning(
                                "Stop qty rounded: %s -> %s (lot_size=%s)",
                                quantity,
                                rounded_qty,
                                props.lot_size,
                            )
                            quantity = Decimal(str(rounded_qty))

                with self._metadata_lock:
                    self._order_metadata[internal_order_id] = {
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "strategy": strategy,
                        "stop_price": stop_price,
                        "submitted_at": datetime.now(timezone.utc),
                    }

                broker_order_id = self.broker.submit_stop_order(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    stop_price=stop_price,
                    internal_order_id=internal_order_id,
                )

                self._submitted_order_ids.add(internal_order_id)
                self._internal_to_broker_id[internal_order_id] = broker_order_id

                # Journal + tx log submit for stop orders too (needed for restart seeding)
                ids = self._trade_ids(internal_order_id)
                self._j_emit(
                    build_trade_event(
                        event_type="ORDER_SUBMIT",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        symbol=symbol,
                        side=side.value,
                        qty=str(quantity),
                        order_type="STOP",
                        strategy=strategy,
                        reason={"source": "engine", "stop_price": str(stop_price)},
                    )
                )
                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_SUBMIT",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "side": side.value,
                                "qty": str(quantity),
                                "order_type": "STOP",
                                "stop_price": str(stop_price),
                                "strategy": strategy,
                            }
                        )
                    except Exception:
                        pass

                if self.order_tracker:
                    tracker_side = TrackerOrderSide.BUY if side == BrokerOrderSide.BUY else TrackerOrderSide.SELL
                    in_flight_order = InFlightOrder(
                        client_order_id=internal_order_id,
                        exchange_order_id=broker_order_id,
                        symbol=symbol,
                        quantity=quantity,
                        side=tracker_side,
                        order_type=OrderType.STOP,
                        price=None,
                        stop_price=stop_price,  # InFlightOrder supports stop_price
                        strategy_id=strategy,
                        submitted_at=datetime.now(timezone.utc),
                    )
                    self.order_tracker.start_tracking(in_flight_order)

                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.SUBMITTED,
                    broker_order_id=broker_order_id,
                )
                return broker_order_id

            except OrderValidationError:
                raise
            except Exception as e:
                self.logger.error(
                    "Stop order submission failed",
                    extra={"internal_order_id": internal_order_id, "symbol": symbol, "error": str(e)},
                    exc_info=True,
                )
                self.state_machine.transition(
                    order_id=internal_order_id,
                    from_state=OrderStatus.PENDING,
                    to_state=OrderStatus.REJECTED,
                    reason=str(e),
                )
                raise OrderExecutionError(f"Failed to submit stop order: {e}") from e

    # ---------------------------------------------------------------------
    # CANCEL
    # ---------------------------------------------------------------------

    def cancel_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        reason: str = "cancelled_by_engine",
    ) -> bool:
        """Cancel an order at the broker and transition state machine."""
        with LogContext(internal_order_id):
            try:
                ok = self.broker.cancel_order(broker_order_id)
                if not ok:
                    return False

                ids = self._trade_ids(internal_order_id)
                symbol = self._order_metadata.get(internal_order_id, {}).get("symbol")
                self._j_emit(
                    build_trade_event(
                        event_type="CANCEL",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        symbol=symbol,
                        reason={"cancel_reason": reason},
                    )
                )

                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": "ORDER_CANCEL",
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": symbol,
                                "reason": reason,
                            }
                        )
                    except Exception:
                        pass

                # best-effort transition to CANCELLED if not terminal
                try:
                    order = self.state_machine.get_order(internal_order_id)
                    from_state = order.state if order else OrderStatus.SUBMITTED
                    if not self.state_machine.is_terminal(from_state):
                        self.state_machine.transition(
                            order_id=internal_order_id,
                            from_state=from_state,
                            to_state=OrderStatus.CANCELLED,
                            broker_order_id=broker_order_id,
                            reason=reason,
                        )
                except Exception:
                    pass

                if self.order_tracker:
                    self.order_tracker.stop_tracking(internal_order_id, reason="cancelled")

                return True

            except Exception as e:
                self.logger.error(
                    "Order cancel failed",
                    extra={"internal_order_id": internal_order_id, "broker_order_id": broker_order_id, "error": str(e)},
                    exc_info=True,
                )
                ids = self._trade_ids(internal_order_id)
                symbol = self._order_metadata.get(internal_order_id, {}).get("symbol")
                self._j_emit(
                    build_trade_event(
                        event_type="ERROR",
                        ids=ids,
                        internal_order_id=internal_order_id,
                        symbol=symbol,
                        error={"where": "OrderExecutionEngine", "message": str(e)},
                    )
                )
                return False

    # ---------------------------------------------------------------------
    # STATUS / WAIT
    # ---------------------------------------------------------------------

    def get_fill_details(self, internal_order_id: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Return (filled_qty, fill_price) if known from state machine."""
        try:
            order = self.state_machine.get_order(internal_order_id)
            if not order:
                return None, None
            return getattr(order, "filled_qty", None), getattr(order, "fill_price", None)
        except Exception:
            return None, None

    def is_order_stale(self, internal_order_id: str, ttl_seconds: int) -> bool:
        """Return True if order age >= ttl_seconds based on engine submission metadata."""
        try:
            with self._metadata_lock:
                meta = self._order_metadata.get(internal_order_id) or {}
                submitted_at = meta.get("submitted_at")
            if not submitted_at:
                return False
            age = (datetime.now(timezone.utc) - submitted_at).total_seconds()
            return age >= float(ttl_seconds)
        except Exception:
            return True

    def poll_order_status(
        self,
        internal_order_id: str,
        broker_order_id: str,
        current_state: OrderStatus,
    ) -> OrderStatus:
        """Poll order status from broker and update state."""
        with LogContext(internal_order_id):
            try:
                broker_status, fill_info = self.broker.get_order_status(broker_order_id)

                if broker_status != current_state:
                    self._handle_status_change(
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        from_state=current_state,
                        to_state=broker_status,
                        fill_info=fill_info,
                    )
                return broker_status
            except Exception as e:
                self.logger.error(
                    "Failed to poll order status",
                    extra={"internal_order_id": internal_order_id, "broker_order_id": broker_order_id, "error": str(e)},
                    exc_info=True,
                )
                return current_state

    def wait_for_order(
        self,
        internal_order_id: str,
        broker_order_id: str,
        timeout_seconds: int = 60,
        poll_interval: float = 1.0,
    ) -> OrderStatus:
        """Wait for order to reach terminal state."""
        with LogContext(internal_order_id):
            start = time.time()
            current_state = OrderStatus.SUBMITTED

            while time.time() - start < timeout_seconds:
                current_state = self.poll_order_status(
                    internal_order_id=internal_order_id,
                    broker_order_id=broker_order_id,
                    current_state=current_state,
                )
                if self.state_machine.is_terminal(current_state):
                    self.logger.info(
                        "Order reached terminal state: %s",
                        current_state.value,
                        extra={
                            "internal_order_id": internal_order_id,
                            "final_state": current_state.value,
                            "elapsed_seconds": time.time() - start,
                        },
                    )
                    return current_state
                time.sleep(poll_interval)

            self.logger.warning(
                "Order polling timeout",
                extra={"internal_order_id": internal_order_id, "timeout_seconds": timeout_seconds, "last_state": current_state.value},
            )
            return current_state

    # ---------------------------------------------------------------------
    # STATUS CHANGE HANDLER
    # ---------------------------------------------------------------------

    def _handle_status_change(
        self,
        internal_order_id: str,
        broker_order_id: str,
        from_state: OrderStatus,
        to_state: OrderStatus,
        fill_info: Optional[Dict[str, Any]],
    ) -> None:
        """
        Handle order status change.

        PATCH 3: Support partial fills by tracking cumulative filled_qty
        and updating positions incrementally.
        """
        filled_qty = None
        fill_price = None

        if fill_info:
            filled_qty = fill_info.get("filled_qty")
            fill_price = fill_info.get("filled_avg_price")

        self.state_machine.transition(
            order_id=internal_order_id,
            from_state=from_state,
            to_state=to_state,
            broker_order_id=broker_order_id,
            filled_qty=filled_qty,
            fill_price=fill_price,
        )

        # PATCH 3: Handle both PARTIALLY_FILLED and FILLED states
        is_fill_event = to_state in (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)

        if is_fill_event and filled_qty and fill_price:
            # Calculate incremental fill quantity
            previous_filled = self._cumulative_filled_qty.get(internal_order_id, Decimal("0"))
            incremental_qty = filled_qty - previous_filled

            if incremental_qty > 0:
                # Update cumulative tracker
                self._cumulative_filled_qty[internal_order_id] = filled_qty

                # Process fill in OrderTracker
                if self.order_tracker:
                    fill_event = FillEvent(
                        timestamp=datetime.now(timezone.utc),
                        quantity=incremental_qty,
                        price=fill_price,
                        commission=Decimal("0"),
                        fill_id=None,
                    )
                    self.order_tracker.process_fill(internal_order_id, fill_event)

                # durable transaction log fill + trade journal fill
                ids = self._trade_ids(internal_order_id)
                fill_symbol = self._order_metadata.get(internal_order_id, {}).get("symbol")

                event_type = "ORDER_PARTIALLY_FILLED" if to_state == OrderStatus.PARTIALLY_FILLED else "ORDER_FILLED"

                if self.transaction_log is not None:
                    try:
                        self.transaction_log.append(
                            {
                                "event_type": event_type,
                                "run_id": ids.run_id,
                                "trade_id": ids.trade_id,
                                "internal_order_id": internal_order_id,
                                "broker_order_id": broker_order_id,
                                "symbol": fill_symbol,
                                "incremental_qty": str(incremental_qty),
                                "cumulative_filled_qty": str(filled_qty),
                                "fill_price": str(fill_price),
                            }
                        )
                    except Exception:
                        pass

                self._j_emit(
                    build_trade_event(
                        event_type=event_type,
                        ids=ids,
                        internal_order_id=internal_order_id,
                        broker_order_id=broker_order_id,
                        symbol=fill_symbol,
                        qty=str(incremental_qty),
                        reason={"fill_price": str(fill_price), "cumulative_filled": str(filled_qty)},
                    )
                )

                # Update position with INCREMENTAL quantity
                self._create_position(internal_order_id, incremental_qty, fill_price)

                # PATCH 1: Handle protective orders ONLY on FINAL fill (not partial)
                if to_state == OrderStatus.FILLED:
                    # If this is a protective leg, cancel sibling (OCO) on final fill
                    if self._is_protective_internal_id(internal_order_id):
                        self._maybe_cancel_oco_sibling_on_fill(
                            internal_order_id=internal_order_id,
                            reason="oco_peer_filled",
                        )

                    # If this is a BUY entry final fill, ensure protective orders are live at broker
                    with self._metadata_lock:
                        meta = self._order_metadata.get(internal_order_id) or {}
                    if meta and (not self._is_protective_internal_id(internal_order_id)):
                        side = meta.get("side")
                        if side == BrokerOrderSide.BUY:
                            # Use cumulative filled_qty for protective order sizing
                            self._ensure_protective_orders_for_entry(
                                entry_internal_order_id=internal_order_id,
                                symbol=meta.get("symbol"),
                                filled_qty=filled_qty,  # cumulative total
                                entry_fill_price=fill_price,
                                strategy=str(meta.get("strategy")),
                                stop_loss=meta.get("stop_loss"),
                                take_profit=meta.get("take_profit"),
                            )

                    # Clean up cumulative tracker on final fill
                    self._cumulative_filled_qty.pop(internal_order_id, None)

    # ---------------------------------------------------------------------
    # POSITION UPDATES
    # ---------------------------------------------------------------------

    def _create_position(
        self,
        internal_order_id: str,
        filled_qty: Decimal,
        fill_price: Decimal,
    ) -> None:
        """
        Create or update position on order fill.

        CRITICAL LOGIC:
        - BUY orders: Add to position (or create if none)
        - SELL orders: Reduce from position (or delete if zero)
        """
        if filled_qty is None or filled_qty <= 0:
            return

        with self._metadata_lock:
            metadata = self._order_metadata.get(internal_order_id)

        if not metadata:
            self.logger.warning(
                "No metadata for order %s",
                internal_order_id,
                extra={"internal_order_id": internal_order_id},
            )
            return

        symbol = metadata["symbol"]
        side = metadata["side"]

        existing_position = self.position_store.get(symbol)

        # CASE 1: BUY order
        if side == BrokerOrderSide.BUY:
            if existing_position is None:
                position = Position(
                    symbol=symbol,
                    quantity=filled_qty,
                    entry_price=fill_price,
                    entry_time=datetime.now(timezone.utc),
                    strategy=metadata["strategy"],
                    order_id=internal_order_id,
                    stop_loss=metadata.get("stop_loss"),
                    take_profit=metadata.get("take_profit"),
                )
                self.position_store.upsert(position)
            else:
                new_quantity = existing_position.quantity + filled_qty
                total_cost = (existing_position.quantity * existing_position.entry_price) + (
                    filled_qty * fill_price
                )
                new_entry_price = total_cost / new_quantity

                position = Position(
                    symbol=symbol,
                    quantity=new_quantity,
                    entry_price=new_entry_price,
                    entry_time=existing_position.entry_time,
                    strategy=existing_position.strategy,
                    order_id=existing_position.order_id,
                    stop_loss=existing_position.stop_loss,
                    take_profit=existing_position.take_profit,
                )
                self.position_store.upsert(position)

        # CASE 2: SELL order
        elif side == BrokerOrderSide.SELL:
            if existing_position is None:
                self.logger.warning(
                    "SELL order filled but no position exists: %s",
                    symbol,
                    extra={"symbol": symbol, "filled_qty": str(filled_qty)},
                )
                return

            new_quantity = existing_position.quantity - filled_qty
            if new_quantity < 0:
                self.logger.error(
                    "Position over-closed: %s had %s, sold %s",
                    symbol,
                    existing_position.quantity,
                    filled_qty,
                    extra={
                        "symbol": symbol,
                        "existing": str(existing_position.quantity),
                        "sold": str(filled_qty),
                    },
                )
                new_quantity = Decimal("0")

            if new_quantity == 0:
                self.position_store.delete(symbol)

                # PATCH 1.1: If we closed the position via a normal exit,
                # cancel any remaining protective SL/TP orders tied to the entry.
                # If this SELL was itself a protective leg, OCO logic already handled sibling cancellation.
                if not self._is_protective_internal_id(internal_order_id):
                    try:
                        entry_id = getattr(existing_position, "order_id", None) or existing_position.order_id
                        self._cancel_protective_orders_for_entry(
                            entry_internal_order_id=entry_id,
                            reason="position_closed_cancel_protection",
                        )
                    except Exception:
                        self.logger.warning(
                            "Failed to cancel protective orders on position close",
                            extra={"symbol": symbol, "internal_order_id": internal_order_id},
                            exc_info=True,
                        )

            else:
                position = Position(
                    symbol=symbol,
                    quantity=new_quantity,
                    entry_price=existing_position.entry_price,
                    entry_time=existing_position.entry_time,
                    strategy=existing_position.strategy,
                    order_id=existing_position.order_id,
                    stop_loss=existing_position.stop_loss,
                    take_profit=existing_position.take_profit,
                )
                self.position_store.upsert(position)

# ============================================================================


class OrderExecutionError(Exception):
    """Order execution error."""


class OrderValidationError(OrderExecutionError):
    """Raised when pre-submission order validation fails."""


class DuplicateOrderError(OrderExecutionError):
    """Raised when attempting to submit duplicate order ID."""
