# Phase 2 — Known Failure Regimes

## Overview

VWAPMicroMeanReversion is a **LONG-only intraday mean-reversion** strategy on SPY.
It profits when price dips below VWAP and reverts.
It **loses** when price continues trending away from VWAP.

This document enumerates known adverse regimes and their expected impact.

---

## 1. Trend Days (REGIME_TREND_DAY)

**Description**: Price moves persistently in one direction all day (e.g., gap-down + sell-off).
The strategy enters on a dip below VWAP expecting reversion, but price never reverts.

**Symptoms**:
- Price stays below VWAP for extended periods (>30 min continuously below VWAP)
- Multiple consecutive bars close below VWAP
- VWAP itself is declining (pulled by persistent selling)

**Impact**: Stop-loss hit or TIMEOUT exit. Expected daily loss = risk_dollars_per_trade.

**Detection heuristic**: Count consecutive bars where close < VWAP. If > threshold (e.g., 15), flag as trend day.

**Mitigation**: Max 1 trade/day already limits exposure. TIMEOUT exit (PATCH 3) prevents indefinite holding.

---

## 2. News / Volatility Spike (REGIME_VOL_SPIKE)

**Description**: Sudden large price move on news (earnings, FOMC, macro). Spread widens, fill quality degrades.

**Symptoms**:
- Bar range (high - low) is >3x the recent average bar range
- Volume spike (>3x average)
- Price gap between consecutive bars

**Impact**: Entry at distorted price, stop-loss hit immediately or large slippage on exit.

**Detection heuristic**: Compare current bar range to rolling average (e.g., 20-bar ATR proxy). If ratio > 3.0, flag.

**Mitigation**: No-trade filter can integrate volatility check. Current implementation does not, but the detection function is provided.

---

## 3. Low-Volume Chop / Lunch Lull (REGIME_LOW_VOLUME)

**Description**: 11:30 AM – 2:00 PM ET typically has lower volume and wider spreads. Mean reversion signals may be noise, not real dislocations.

**Symptoms**:
- Volume drops below 50% of first-hour average
- Price oscillates in narrow range without directional conviction
- Multiple small false breakdowns below VWAP that revert immediately

**Impact**: Entry fills are marginal, exits are marginal. Small P&L but high opportunity cost and slippage.

**Mitigation**: Default trade window (10:00–11:30 ET) avoids this regime. No additional detection needed unless window is expanded.

---

## 4. Opening Volatility (REGIME_OPEN_VOL)

**Description**: First 5–15 minutes after market open (9:30–9:45 ET). Wide spreads, erratic price discovery, VWAP not yet stable.

**Symptoms**:
- VWAP has fewer than min_bars worth of data
- Bar-to-bar price swings are exaggerated
- Volume concentrated in first few bars

**Impact**: VWAP calculation unreliable, entry at wrong level.

**Mitigation**: `vwap_min_bars` warmup prevents trading before VWAP is stable. Blackout near open (no_trade_filter.py) provides additional protection.

---

## 5. Macro Windows / Blackout Periods (REGIME_MACRO_WINDOW)

**Description**: Scheduled events (FOMC announcement, NFP, CPI release) cause uncertainty. Market may freeze then spike.

**Symptoms**:
- Known calendar events (external data required)
- Volume may drop just before, spike on release
- Large directional moves post-announcement

**Impact**: Strategy may enter just before a large adverse move.

**Mitigation**: Calendar-based blackout is best practice but requires external data feed. Current implementation does not have this. The no_trade_filter framework supports adding REGIME_NOT_ALLOWED reason codes if calendar integration is added later.

---

## Detection Functions

Deterministic heuristics are provided in `strategies/regime_detection.py`:

| Function | Regime | Inputs | Output |
|---|---|---|---|
| `detect_trend_day()` | REGIME_TREND_DAY | consecutive bars below VWAP | bool + bar count |
| `detect_volatility_spike()` | REGIME_VOL_SPIKE | current range, rolling avg range | bool + ratio |

These are **offline-safe**: they use only bar data (no wall clock, no external feeds).
They can be integrated into the no-trade filter as optional checks.

---

## Testing

Tests in `tests/p2/test_failure_regimes.py` validate:
1. `detect_trend_day()` correctly identifies persistence below VWAP
2. `detect_volatility_spike()` correctly identifies range expansion
3. Normal conditions do not trigger false positives
