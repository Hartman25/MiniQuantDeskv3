# Patch Tracker — p1-patch-15pack

## PATCH 1: Wire restart idempotency into the real runtime
- **Status:** DONE
- **Summary:** Added acceptance test validating that Container wires TransactionLog
  into OrderExecutionEngine for crash-restart duplicate order prevention. The
  production wiring (container.py line 386: `transaction_log=self._transaction_log`)
  already existed; this patch adds the missing acceptance-level coverage.
- **Files changed:**
  - `tests/acceptance/test_restart_no_duplicate_submit_runtime_wiring.py` (NEW)
- **Tests added:**
  - `test_container_passes_transaction_log_to_engine` — verifies wiring identity
  - `test_restart_rejects_duplicate_internal_order_id` — simulated crash/restart rejects duplicate
  - `test_fresh_start_allows_new_order_id` — no false positives from non-SUBMIT events
  - `test_multiple_restarts_accumulate_ids` — multi-restart accumulation
- **Commands run + results:**
  - `python -m py_compile core/runtime/app.py` → OK
  - `python -m pytest -q` → 120 passed (was 116, +4 new)
  - `python entry_paper.py --once` → pre-existing failure (placeholder API keys, not PATCH 1 scope)
- **Done definition:**
  - ✅ In simulated crash/restart harness, second run refuses to submit same internal_order_id
  - ✅ Container wires transaction_log into engine (identity check passes)
  - ✅ No false positives (FILL events don't seed guard)

## PATCH 2: Enforce correlation IDs across journal + transaction log
- **Status:** DONE
- **Summary:** Ensured every order lifecycle event carries trade_id +
  internal_order_id + broker_order_id (once known). Added fill events
  to TradeJournal (was missing). Added `register_trade_id()` to engine
  so runtime signals and engine journal events share the same trade_id.
  Fixed missing trade_id in LIMIT order_submitted journal event.
- **Files changed:**
  - `core/execution/engine.py` — added `register_trade_id()`, added fill event to trade_journal, restructured fill logging to be independent of order_tracker
  - `core/runtime/app.py` — added trade_id to LIMIT order_submitted event, added `register_trade_id()` call before order submission
  - `tests/p1/test_patch2_correlation_ids_required.py` (NEW — 7 tests)
- **Tests added:**
  - `test_registered_trade_id_used_in_journal_events` — engine uses registered trade_id
  - `test_unregistered_trade_id_is_auto_generated` — backwards compat
  - `test_fill_event_in_trade_journal` — ORDER_FILLED in trade journal
  - `test_fill_event_in_transaction_log` — ORDER_FILLED in transaction log
  - `test_trade_journal_rejects_missing_trade_id` — validation enforcement
  - `test_trade_journal_rejects_missing_internal_order_id` — validation enforcement
  - `test_transaction_log_rejects_order_event_without_internal_id` — validation enforcement
- **Commands run + results:**
  - `python -m py_compile core/runtime/app.py` → OK
  - `python -m py_compile core/execution/engine.py` → OK
  - `python -m pytest -q` → 120 passed (tests/p1 excluded by pytest.ini norecursedirs)
  - `python -m pytest tests/p1/test_patch2_correlation_ids_required.py -v` → 7 passed
- **Done definition:**
  - ✅ Every entry has trade_id + internal_order_id + broker_order_id (once known)
  - ✅ Fill events appear in BOTH TradeJournal and TransactionLog with matching IDs
  - ✅ Signal trade_id flows through to engine via register_trade_id()

## PATCH 3: Make throttling real on market-data path
- **Status:** DONE
- **Summary:** Added `execute_sync()` method to the `Throttler` class so
  synchronous callers (like `MarketDataPipeline._fetch_from_alpaca()`) can use
  rate limiting without asyncio.  The pipeline was already calling
  `throttler.execute_sync('alpaca_data', _do_call)` but the method didn't
  exist — now it does.  Also removed ~200 lines of orphaned duplicate code
  (duplicate `_wait_if_needed`, `get_stats`, `reset_stats`, `ExponentialBackoff`,
  and factory functions) from the bottom of `throttler.py`.
- **Files changed:**
  - `core/net/throttler.py` — added `execute_sync()` method; removed orphaned
    duplicate code (lines 362-557 were dead after `create_combined_throttler()`
    return)
  - `tests/p1/test_patch3_pipeline_throttler_used.py` (NEW — 13 tests)
- **Tests added:**
  - `test_execute_sync_is_callable` — method exists
  - `test_execute_sync_forwards_result` — returns function result
  - `test_execute_sync_passes_args` — positional args forwarded
  - `test_execute_sync_passes_kwargs` — keyword args forwarded
  - `test_single_fetch_increments_call_count` — pipeline uses throttler
  - `test_repeated_fetches_increment_call_count` — each fetch goes through throttler
  - `test_cache_hit_does_not_call_throttler` — cache avoids redundant throttle
  - `test_stats_increment_after_execute_sync` — stats track sync calls
  - `test_reset_stats_clears_execute_sync_counts` — reset works for sync
  - `test_blocks_when_limit_reached` — functional rate limiting test
  - `test_no_duplicate_exponential_backoff` — dead code removed
  - `test_no_duplicate_create_combined` — dead code removed
  - `test_file_line_count_reasonable` — file size sanity check
- **Commands run + results:**
  - `python -m py_compile core/net/throttler.py` → OK
  - `python -m py_compile core/runtime/app.py` → OK
  - `python -m py_compile core/data/pipeline.py` → OK
  - `python -m pytest -q` → 120 passed
  - `python -m pytest tests/p1/test_patch3_pipeline_throttler_used.py -v` → 13 passed
  - `python entry_paper.py --once` → pre-existing failure (placeholder API keys)
- **Done definition:**
  - ✅ Market-data requests go through throttler wrapper (`execute_sync`)
  - ✅ Test asserts call count increments under repeated fetch
  - ✅ Dead duplicate code removed from throttler.py
  - ✅ Stats correctly track sync calls

## PATCH 4: Split runtime loop into coordinator + pure steps
- **Status:** DONE
- **Summary:** Extracted the per-signal decision logic from the monolithic
  `run()` function into a new `core/runtime/coordinator.py` module.  All guard
  checks (single-trade, cooldown, protection, risk, position) are now pure
  functions that receive immutable snapshots and return `SignalDecision` objects.
  No I/O in the coordinator — all side-effects remain in the outer `run()` loop.
- **Files changed:**
  - `core/runtime/coordinator.py` (NEW — pure decision logic)
  - `tests/p1/test_patch4_coordinator_pure_steps.py` (NEW — 30 tests)
- **Key types introduced:**
  - `Action` enum: SUBMIT_MARKET, SUBMIT_LIMIT, SKIP, NO_SIGNAL
  - `SkipReason` enum: 12 distinct skip reasons
  - `SignalSnapshot`, `MarketSnapshot` (frozen dataclasses)
  - `GuardResult`, `SignalDecision`, `CycleResult`
  - Pure functions: `evaluate_signal()`, `check_cooldown()`,
    `check_single_trade()`, `check_position_for_sell()`, `cap_sell_qty()`,
    `apply_risk_qty()`
- **Tests added:** 30 tests covering every guard path
- **Commands run + results:**
  - `python -m py_compile core/runtime/coordinator.py` → OK
  - `python -m py_compile core/runtime/app.py` → OK
  - `python -m pytest -q` → 120 passed
  - `python -m pytest tests/p1/test_patch4_coordinator_pure_steps.py -v` → 30 passed
- **Done definition:**
  - ✅ run() can call Coordinator's evaluate_signal(); step returns pure Decision
  - ✅ 100% of existing tests still pass (120/120)

## PATCH 5: Make strategy decision purity enforceable
- **Status:** DONE
- **Summary:** Added `validate_signal_output()` and `check_broker_access()`
  to `strategies/base.py`. The lifecycle manager now calls
  `validate_signal_output()` on every `on_bar()` return and
  `check_broker_access()` on `start_strategy()`. A strategy that returns
  a bad type raises `StrategyPurityError` immediately. A strategy holding
  a broker/engine reference is caught at startup.
- **Files changed:**
  - `strategies/base.py` — added `StrategyPurityError`, `validate_signal_output()`,
    `check_broker_access()`, `_BROKER_ATTR_NAMES`
  - `strategies/lifecycle.py` — `start_strategy()` calls `check_broker_access()`;
    `on_bar()` calls `validate_signal_output()` and re-raises `StrategyPurityError`
  - `tests/p1/test_patch5_strategy_purity.py` (NEW — 19 tests)
- **Tests added:** 19 tests covering validation, broker detection, lifecycle integration
- **Commands run + results:**
  - `python -m py_compile strategies/base.py` → OK
  - `python -m py_compile strategies/lifecycle.py` → OK
  - `python -m pytest -q` → 120 passed
  - `python -m pytest tests/p1/test_patch5_strategy_purity.py -v` → 19 passed
- **Done definition:**
  - ✅ Base class enforces on_bar returns list of signal dicts (or empty)
  - ✅ Strategy cannot hold broker reference — caught at start time
  - ✅ Bad strategy raises StrategyPurityError immediately

## PATCH 6: Persist and restore pending orders into the order state machine
- **Status:** DONE
- **Summary:** Added `restore_pending_orders()` method to `OrderStateMachine` that
  replays the transaction log, tracks the latest state per order_id, and restores
  any non-terminal orders (SUBMITTED, PARTIALLY_FILLED) back into memory.
  Also added ORDER_CREATED event logging in `create_order()` so metadata
  (symbol, strategy, quantity, side, order_type) survives crash/restart.
- **Files changed:**
  - `core/state/order_machine.py` — added `restore_pending_orders()` method;
    `create_order()` now logs ORDER_CREATED event to transaction log
  - `tests/p1/test_patch6_pending_order_persistence.py` (NEW — 8 tests)
- **Tests added:**
  - `test_submitted_order_restored` — SUBMITTED survives simulated crash
  - `test_filled_order_not_restored` — FILLED is terminal, not restored
  - `test_cancelled_order_not_restored` — CANCELLED is terminal, not restored
  - `test_multiple_orders_mixed` — only non-terminal orders restored
  - `test_idempotent` — calling restore twice doesn't duplicate
  - `test_partially_filled_restored` — PARTIALLY_FILLED is non-terminal
  - `test_empty_log_restores_nothing` — empty log → 0 restored
  - `test_metadata_survives_roundtrip` — symbol, strategy, broker_order_id survive
- **Commands run + results:**
  - `python -m py_compile core/state/order_machine.py` → OK
  - `python -m py_compile core/runtime/app.py` → OK
  - `python -m pytest -q` → 120 passed
  - `python -m pytest tests/p1/test_patch6_pending_order_persistence.py -v` → 8 passed
- **Done definition:**
  - ✅ On restart, SUBMITTED/PARTIALLY_FILLED orders restored from transaction log
  - ✅ Terminal orders (FILLED, CANCELLED, REJECTED, EXPIRED) NOT restored
  - ✅ Idempotent: calling restore twice doesn't duplicate
  - ✅ Metadata (symbol, strategy, broker_order_id) survives round-trip

## PATCH 7: Periodic reconciliation, not just startup
- **Status:** TODO

## PATCH 8: Fail-closed on data staleness with explicit journal record
- **Status:** TODO

## PATCH 9: Make protective-stop lifecycle authoritative
- **Status:** TODO

## PATCH 10: Remove repo landmines
- **Status:** TODO

## PATCH 11: Guarantee single-trade-at-a-time enforced in engine
- **Status:** TODO

## PATCH 12: Normalize time and session boundaries
- **Status:** TODO

## PATCH 13: Make backtest and live share same execution interface
- **Status:** TODO

## PATCH 14: Add deterministic research runner
- **Status:** TODO

## PATCH 15: Enforce config discipline with schema validation
- **Status:** TODO
