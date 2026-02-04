# VWAPMicroMeanReversion - Known Failure Regimes

This document catalogues known failure regimes for the
VWAPMicroMeanReversion strategy and the mitigations in place.

---

## 1. Gap Open / Gap Down

**Scenario:** Overnight news causes the market to open significantly
above or below the prior close. VWAP is computed from the open, so
the intraday VWAP can be disconnected from the prior day's price
structure.

**Impact:** Entry signals fire on what appears to be a VWAP deviation,
but the deviation is actually a structural gap. Stop losses are hit
quickly because the "reversion" never comes.

**Mitigations:**
- Trade window starts at 10:00 ET (30 min after open), allowing the
  initial gap volatility to settle.
- Daily loss limit ($2.50 default) caps total exposure on gap days.
- Max 1 trade per day prevents repeated gap-related losses.

---

## 2. Low Liquidity / Wide Spreads

**Scenario:** Pre-market or thinly traded periods produce bars with
large bid-ask spreads. VWAP calculation using typical price may be
skewed, and fills suffer significant slippage.

**Impact:** Entry at a worse price than expected. Slippage erodes
the small edge that mean reversion provides.

**Mitigations:**
- Trade window (10:00-11:30 ET) targets the highest-liquidity period.
- SPY-only constraint ensures deep order books.
- Limit orders with configurable offset (entry_limit_offset_bps).
- TTL on limit orders (default 90s) prevents stale fills.

---

## 3. VWAP Unreliable on Low Volume

**Scenario:** Early bars have very low volume, causing the cumulative
VWAP to be dominated by a few trades with potentially unrepresentative
prices.

**Impact:** False deviation signals because VWAP is not yet stable.

**Mitigations:**
- Warmup period (vwap_min_bars=20) requires sufficient bar count
  before any signal is emitted.
- Volume-weighted VWAP (typical_price * volume) naturally discounts
  low-volume bars.

---

## 4. Volatility Spike / Flash Crash

**Scenario:** Sudden intraday volatility expansion (e.g., breaking
news, flash crash) causes rapid price moves that exceed normal
deviation thresholds and blow through stop losses before orders fill.

**Impact:** Stop loss orders may fill at worse prices than expected.
Multiple rapid entries/exits drain the daily loss limit.

**Mitigations:**
- Risk-based position sizing: qty = risk_dollars / (price * stop_pct),
  so dollar risk per trade is capped regardless of volatility.
- Daily loss limit halts the strategy after cumulative loss threshold.
- Max trades per day (1) prevents repeated whipsaw entries.
- Circuit breaker at runtime level halts all trading after consecutive
  failures.

---

## 5. Trending Market (No Mean Reversion)

**Scenario:** The market trends strongly in one direction throughout
the day. Price never reverts to VWAP after entry, and the strategy
sits in a losing position until stop loss or EOD force-flat.

**Impact:** Strategy enters on what looks like a deviation, but it's
actually a trend. Holding until force-flat at 15:55 locks in losses.

**Mitigations:**
- Max time-in-trade (default 60 minutes) forces exit before EOD if
  the position hasn't resolved.
- Stop loss (0.3% default) limits per-trade downside.
- Strategy is long-only in SPY (which trends up on average), so
  trending down days are the failure case, not trending up.

---

## 6. Daily Loss Limit Interaction with PDT

**Scenario:** Pattern Day Trader (PDT) rules restrict accounts under
$25k to 3 day trades per 5 rolling business days. The strategy's
daily loss limit may be hit in fewer trades than PDT allows, but on
days with frequent stop-outs, PDT limits may bind first.

**Mitigations:**
- Max 1 trade per day (default) means PDT is never hit in normal
  operation (1 trade/day = 5/week, well within PDT limits).
- PreTradeRiskGate checks PDT order count before submission.
- Micro account sizing ($1.50 risk) keeps the account above the $100
  minimum for fractional share trading.

---

## Summary Matrix

| Regime | Probability | Severity | Primary Mitigation |
|--------|------------|----------|-------------------|
| Gap open | Medium | Medium | 10:00 ET start, daily loss limit |
| Low liquidity | Low (SPY) | Low | Trade window, limit orders |
| VWAP low volume | Medium | Low | Warmup period (20 bars) |
| Volatility spike | Low | High | Risk sizing, circuit breaker |
| Trending market | Medium | Medium | Max time-in-trade, stop loss |
| PDT interaction | Low | Medium | Max 1 trade/day, risk gate |
