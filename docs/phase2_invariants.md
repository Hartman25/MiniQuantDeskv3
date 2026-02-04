# Phase 2 Invariants — Strategy Correctness

## Overview

Phase 2 validates that **strategy logic is correct, deterministic, and observable**.
Phase 1 guarantees execution integrity (orders flow correctly).
Phase 2 guarantees the *decisions* feeding that execution are sound.

---

## Invariant Table

| ID | Invariant | Description | Enforcement Location | Test File(s) |
|---|---|---|---|---|
| P2-INV-01 | LONG-only enforcement | VWAPMicroMeanReversion never emits SELL without an open position (no naked shorts). | `strategies/vwap_micro_mean_reversion.py:on_bar` | `tests/p2/test_vwap_correctness.py` |
| P2-INV-02 | VWAP reset on new day | VWAP state (pv_sum, v_sum) resets when bar date changes. Stale VWAP from prior day never used for entry. | `strategies/vwap_micro_mean_reversion.py:_reset_if_new_day` | `tests/p2/test_vwap_correctness.py` |
| P2-INV-03 | Entry only below deviation | Entry signal emitted only when `price < VWAP * (1 - entry_deviation_pct)`. | `strategies/vwap_micro_mean_reversion.py:on_bar` | `tests/p2/test_vwap_correctness.py` |
| P2-INV-04 | No entry within deviation | When price is within deviation band (VWAP - threshold < price < VWAP), no entry signal. | `strategies/vwap_micro_mean_reversion.py:on_bar` | `tests/p2/test_vwap_correctness.py` |
| P2-INV-05 | NO-TRADE reasons are first-class | When entry is blocked, a structured `NoTradeReason` is produced (not silent None). | `strategies/no_trade_filter.py` | `tests/p2/test_no_trade_filter.py` |
| P2-INV-06 | Session window enforcement | No entry signals outside configured trade_start_time..trade_end_time. | `strategies/no_trade_filter.py` | `tests/p2/test_no_trade_filter.py` |
| P2-INV-07 | Blackout near open/close | No entry within configurable blackout minutes of market open/close. | `strategies/no_trade_filter.py` | `tests/p2/test_no_trade_filter.py` |
| P2-INV-08 | Post-stop cooldown | After a stop-loss exit, no new entry for configurable cooldown bars. | `strategies/no_trade_filter.py` | `tests/p2/test_no_trade_filter.py` |
| P2-INV-09 | Max time-in-trade | Position held longer than `max_time_in_trade_minutes` triggers TIMEOUT exit. Uses bar timestamp, never wall clock. | `strategies/vwap_micro_mean_reversion.py:on_bar` | `tests/p2/test_max_time_in_trade.py` |
| P2-INV-10 | TIMEOUT exit reason | Time-limited exit produces `reason="TIMEOUT"` in the exit signal. | `strategies/vwap_micro_mean_reversion.py:on_bar` | `tests/p2/test_max_time_in_trade.py` |
| P2-INV-11 | Failure regime documentation | Known adverse regimes (trend days, vol spikes, chop, macro) documented with expected behavior. | `docs/phase2_failure_regimes.md` | `tests/p2/test_failure_regimes.py` |
| P2-INV-12 | Retirement determinism | Strategy retired when rolling expectancy < threshold OR rolling drawdown > threshold. Retired strategy emits no new entries. | `strategies/retirement.py` | `tests/p2/test_retirement.py` |
| P2-INV-13 | Retirement allows exits | A retired strategy may still emit exit signals for existing positions. | `strategies/retirement.py` | `tests/p2/test_retirement.py` |
| P2-INV-14 | Attribution by dimension | TradeAttributionAnalyzer returns `list[AttributionBreakdown]` by strategy, symbol, time bucket. Deterministic ordering. | `core/analytics/attribution.py` | `tests/p2/test_attribution.py` |
| P2-INV-15 | Signal vs execution timing | Attribution separates signal_time from entry_time/exit_time. Slippage = signal_price - fill_price. | `core/analytics/attribution.py` | `tests/p2/test_attribution.py` |
| P2-INV-16 | Empty analyzer returns [] | TradeAttributionAnalyzer with no trades returns empty list for all attribution queries. | `core/analytics/attribution.py` | `tests/p2/test_attribution.py` |

---

## Failure Modes

Each invariant has a documented failure mode:

- **P2-INV-01 violated**: System opens naked short, unlimited loss exposure.
- **P2-INV-02 violated**: Entry decisions use stale prior-day VWAP, wrong deviation.
- **P2-INV-03/04 violated**: Entries at wrong prices, strategy alpha destroyed.
- **P2-INV-05 violated**: Silent None returns make debugging impossible.
- **P2-INV-06/07 violated**: Trades in illiquid periods, wide spreads, slippage.
- **P2-INV-08 violated**: Revenge trading after stop-out, compounding losses.
- **P2-INV-09/10 violated**: Hung positions, unexpected overnight exposure.
- **P2-INV-11 violated**: Strategy trades blindly in adverse conditions.
- **P2-INV-12/13 violated**: Losing strategy runs forever; or retirement blocks needed exits.
- **P2-INV-14/15/16 violated**: Attribution data unreliable, optimization misguided.
