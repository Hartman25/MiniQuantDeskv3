# Execution Invariants

This document defines the execution guarantees that the trading system
enforces at all times.  Every invariant listed here has a corresponding
test under `tests/p1/`.  If an invariant is violated at runtime the
system MUST halt (return exit code 1) unless explicitly marked as
**fail-open**.

---

## 1. Order State Machine

The order lifecycle is fully deterministic.  All valid transitions are
enumerated in `core/state/order_machine.py:VALID_TRANSITIONS`:

```
PENDING  → SUBMITTED          (broker acknowledged)
PENDING  → REJECTED           (risk gate rejected, no broker confirmation needed)
SUBMITTED → FILLED            (fully executed)
SUBMITTED → PARTIALLY_FILLED  (partial execution)
SUBMITTED → CANCELLED         (cancelled at broker)
SUBMITTED → REJECTED          (broker rejected)
SUBMITTED → EXPIRED           (timed out)
PARTIALLY_FILLED → FILLED     (remaining filled)
PARTIALLY_FILLED → CANCELLED  (remaining cancelled)
```

**Enforcement:**
- Any transition NOT in this set raises `InvalidTransitionError`.
- Any transition FROM a terminal state (FILLED, CANCELLED, REJECTED,
  EXPIRED) raises `TerminalStateError`.
- Thread-safe via internal lock.

**Tests:** `tests/p1/phase1_blocking/test_b1_deterministic_order_lifecycle.py`

---

## 2. Single Position Per Symbol

The system enforces at most one open position per symbol.

**Enforcement layers:**
1. **Runtime loop** (`app.py`): Before submitting a BUY order, checks
   `position_store.has_open_position(symbol)`.  If True, the signal is
   skipped with `RISK_SKIP_ALREADY_IN_POSITION`.
2. **`_single_trade_should_block_entry()`**: Checks position store,
   order store, and broker connector.  Returns True if entry should
   be blocked.
3. **SELL quantity capping**: SELL quantity is capped to the current
   position size to prevent over-selling.

**Fail mode:** Fail-open by default (`fail_closed=False`).  If the
position check itself throws an exception, the trade is allowed
through rather than crashing the system.  Set `fail_closed=True`
for live mode if you prefer to block on uncertainty.

**Tests:** `tests/p1/phase1_blocking/test_b2_single_position_enforcement.py`,
`tests/patch3/test_02_blocks_buy_if_already_in_position.py`

---

## 3. Duplicate Order Prevention

The execution engine prevents re-submission of the same
`internal_order_id` within a session AND across restarts.

**Enforcement:**
- `OrderExecutionEngine._submitted_order_ids` (in-memory set)
  blocks same-session duplicates with `DuplicateOrderError`.
- On construction with a `transaction_log`, the engine replays
  `ORDER_SUBMIT` events to seed the set, covering crash+restart.

**Tests:** `tests/p1/test_patch2_duplicate_order_guard.py`,
`tests/p1/test_patch2_duplicate_order_restart.py`

---

## 4. Circuit Breaker

After `MAX_CONSECUTIVE_FAILURES` (default 5) consecutive unhandled
exceptions in the main loop, the runtime halts with exit code 1.

**Enforcement:**
- `ConsecutiveFailureBreaker` in `core/runtime/circuit_breaker.py`.
- `record_failure()` on exception, `record_success()` on clean cycle.
- `is_tripped` → `return 1`.
- Threshold configurable via `MAX_CONSECUTIVE_FAILURES` env var.

**Additional halt triggers:**
- Recovery FAILED → immediate halt before entering loop.
- Live-mode reconciliation discrepancies → immediate halt.
- `run_once=True` + any exception → exit 1.

**Tests:** `tests/p1/test_patch1_circuit_breaker.py`,
`tests/p1/phase1_blocking/test_b3_invariant_violation_halt.py`

---

## 5. Recovery on Restart

Before the main loop starts, the runtime calls
`_try_recovery(broker, position_store, order_machine)` which:

1. Creates a `StatePersistence` pointed at `STATE_DIR`.
2. Instantiates `RecoveryCoordinator` with persistence + broker.
3. Calls `coordinator.recover()` which:
   - Loads the latest state snapshot (if any).
   - Validates against broker positions/orders.
   - Rebuilds state from broker if no saved state or stale.
4. Returns `RecoveryStatus`: SUCCESS, PARTIAL, REBUILT, or FAILED.

If FAILED, the runtime halts immediately (exit code 1).
If the coordinator itself throws, the exception is caught and
`REBUILT` is returned (fail-open: better to start fresh than crash).

**Tests:** `tests/p1/test_patch1_crash_recovery_validation.py`,
`tests/p1/test_patch7_recovery_coordinator_wiring.py`

---

## 6. Protective Stop Persistence

After a crash+restart, protective stop orders are reloaded from the
broker via `_load_protective_stops_from_broker(broker)`:

- Queries `broker.list_open_orders()`.
- Filters for `order_type=stop` + `side=sell`.
- Returns `{SYMBOL: broker_order_id}`.
- Symbols are uppercased for consistent matching.

**Fail mode:** Fail-open.  If the broker query fails or the method
is missing, an empty dict is returned.  The worst case is placing
a duplicate stop (which the single-trade guard blocks anyway).

On exit signals, the runtime cancels the protective stop before
submitting the sell order, and removes the entry from the map.

**Tests:** `tests/p1/test_patch3_protective_stop_persistence.py`,
`tests/p1/test_patch3_stop_persistence_restart.py`

---

## 7. TTL / Cancellation

LIMIT orders have a TTL (default 90 seconds, overridable per signal
via `ttl_seconds`).  After TTL expires:

- `exec_engine.cancel_order()` is called.
- The order transitions to CANCELLED.
- No protective stop is placed for unfilled entries.

**Tests:** `tests/patch2/test_03_limit_ttl_cancels_unfilled.py`

---

## 8. Idempotent Event Replay

`IdempotentReplayHandler` deduplicates events by composite key
`(event_type, internal_order_id)` or `(event_type, _logged_at)`.
The user-supplied callback is invoked only for first-seen events.

This ensures that replaying a transaction log after restart does
not produce duplicate side effects.

**Tests:** `tests/p1/test_patch6_replay_idempotency.py`
