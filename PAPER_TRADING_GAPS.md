# Paper Trading Truthfulness — Known Gaps

Last updated: 2026-02-07 (Phase 1 hardening)

## What IS truthful

| Guarantee | How it works |
|-----------|-------------|
| **Realized PnL gating** | SELL fills compute `(fill_price - entry_price) * qty` from actual broker fills, recorded to `PersistentLimitsTracker` (SQLite-backed, survives restarts). |
| **Daily loss limit enforcement** | `PreTradeRiskGate` checks `limits_tracker.is_daily_loss_limit_breached()` before every order. Uses realized PnL, not signal intent. |
| **Fill-based position tracking** | Positions store actual fill prices from broker, not signal prices. |
| **Order lifecycle tracking** | Correlation IDs, transaction log, and journal track every order from signal → submit → fill/cancel. |

## What is NOT modeled (paper will look better than live)

### 1. Commissions and fees — LOW severity
Alpaca charges zero equity commissions. SEC fee (~$0.0000221/dollar on sells) is not deducted.
**Impact**: ~0.002% overstatement per sell trade. Negligible for strategy validation.

### 2. Slippage — MEDIUM severity
Paper fills use the broker-reported fill price (which for Alpaca paper is essentially the last trade price). No slippage model is applied.
**Impact**: Live execution will typically slip 5–20 bps on liquid names, more on illiquid. Over many trades, paper PnL will overstate live by 10–50 bps cumulative.
**Note**: Backtest engine (`backtest/fill_models.py`) HAS slippage models. Paper does not.

### 3. Bid-ask spread — MEDIUM severity
Paper orders fill at a single price. Live orders cross the spread.
**Impact**: 5–50 bps per round trip depending on liquidity.
Combined with slippage, expect paper to overstate live results by 20–100 bps per round trip.

### 4. Partial fills — MEDIUM severity
Paper treats each order as a single fill event. Live orders may partially fill over hours.
**Impact**: Average fill price in live may differ from paper. Multi-fill cost averaging is not tracked.

### 5. Market impact — LOW severity (at current scale)
No market impact model. Large orders fill instantly in paper.
**Impact**: Irrelevant at current position sizes (<$100k). Would matter at scale.

## Honest assessment

Paper trading is useful for validating **strategy logic** and **system correctness**.
Paper trading is NOT sufficient for validating **profit estimates**.

Expect live PnL to be **20–100 bps worse per round trip** than paper shows,
depending on stock liquidity, order size, and market conditions.

## TODO: Future hardening (Phase 2+)

- [ ] Apply `AlpacaFeeModel` from `backtest/fee_models.py` to paper PnL recording
- [ ] Add configurable slippage deduction (constant or volume-share model)
- [ ] Track partial fills as separate PnL events
- [ ] Add spread cost estimator using recent bid-ask data
- [ ] Surface paper-vs-live divergence metrics in dashboard
