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
- **Status:** TODO

## PATCH 3: Make throttling real on market-data path
- **Status:** TODO

## PATCH 4: Split runtime loop into coordinator + pure steps
- **Status:** TODO

## PATCH 5: Make strategy decision purity enforceable
- **Status:** TODO

## PATCH 6: Persist and restore pending orders into the order state machine
- **Status:** TODO

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
