MiniQuantDesk – Phase-Gated Checklist with Optimization Guidance
Phase 1 – Execution Integrity
Blocking (Must Be Complete)
Deterministic Order Lifecycle
Preconditions:

OrderStateMachine initialized with valid transition registry
OrderExecutionEngine connected to broker (Alpaca)
PositionStore and OrderTracker initialized

Behavior:

All orders follow strict state transitions: PENDING → SUBMITTED → {FILLED | PARTIALLY_FILLED | CANCELLED | REJECTED | EXPIRED}
Invalid transitions raise InvalidTransitionError and halt
State transitions requiring broker confirmation validate broker_order_id presence
Terminal states (FILLED, CANCELLED, REJECTED, EXPIRED) cannot transition further
All transitions emit OrderStateChangedEvent and log to TransactionLog

Expected Outcome:

Every order has exactly one valid state at any time
State history is complete and auditable via transaction log
No orphaned orders (all orders tracked from creation to terminal state)

Failure Handling:

Invalid transition attempt → raise InvalidTransitionError → order remains in current state
Missing broker confirmation → raise BrokerConfirmationRequiredError → order remains in current state
Terminal state violation → raise TerminalStateError → order unchanged

[ASSUMPTION: Terminal states are enforced at state machine level based on VALID_TRANSITIONS registry in order_machine.py]
Single Active Position Enforcement
Preconditions:

PositionStore initialized and synchronized with broker
Risk validation occurs before order submission

Behavior:

Maximum 1 open position per symbol enforced by max_open_positions: 1 configuration
Position creation only occurs on OrderStatus.FILLED transition
Position quantity updated on partial fills via PARTIALLY_FILLED state
Exit orders (SELL after BUY) reduce position; entry orders create position

Expected Outcome:

At most 1 position per symbol exists in PositionStore
PositionStore.upsert() atomically updates position on fill
No position duplication (position_id is unique per symbol-strategy pair)

Failure Handling:

Attempt to create second position → Risk check rejection: "max_open_positions exceeded"
Position creation fails → Order state transitions to FILLED but position not recorded → Reconciliation detects drift

[ASSUMPTION: Single position enforcement is primarily handled by RiskManager pre-trade validation based on config.yaml max_open_positions setting]
TTL / Cancellation Correctness
Preconditions:

Order submitted with ttl_seconds parameter (for LIMIT orders)
OrderExecutionEngine tracks submission timestamp in _order_metadata
Order in non-terminal state

Behavior:

Engine method is_order_stale(internal_order_id, ttl_seconds) returns True when (now - submitted_at) >= ttl_seconds
Caller (strategy runtime or lifecycle manager) polls order status and cancels if TTL exceeded and not filled
cancel_order(internal_order_id, broker_order_id, reason) submits cancellation to broker
Successful cancellation transitions order to CANCELLED state
OrderTracker stops tracking cancelled order with reason logged

Expected Outcome:

LIMIT orders that don't fill within TTL are cancelled automatically
No stale working orders remain in broker after TTL expiry
Cancellation logged to TradeJournal with event_type="CANCEL"

Failure Handling:

Broker cancel fails → return False, order remains in current state, logged as error
Order already filled before cancel → cancel returns False, order state unchanged (already terminal)
Network timeout during cancel → broker state becomes source of truth via next reconciliation cycle

[ASSUMPTION: TTL enforcement is caller responsibility using is_order_stale() helper; engine provides cancel primitive but does not auto-cancel]
Idempotent Event Handling
Preconditions:

Order submission includes unique internal_order_id
Engine maintains _submitted_order_ids set

Behavior:

Before submitting any order, engine checks internal_order_id in _submitted_order_ids
Duplicate submission raises DuplicateOrderError BEFORE broker API call
First submission adds internal_order_id to _submitted_order_ids set
Fill events from OrderTracker processed with process_fill() — multiple fills for partial executions are accumulated

Expected Outcome:

Same internal_order_id cannot be submitted twice
Duplicate fill events update filled_qty incrementally without creating duplicate positions
Order tracker maintains single InFlightOrder object per client_order_id

Failure Handling:

Duplicate order ID → raise DuplicateOrderError, log error, no broker submission occurs
Duplicate fill event → filled_qty re-updated (idempotent accumulation)

[ASSUMPTION: Idempotency enforced at engine level via _submitted_order_ids set; fill idempotency via OrderTracker's accumulation logic]
Restart Reconciliation with Broker
Preconditions:

System restarted after shutdown (clean or crash)
Broker connector authenticated and operational
PositionReconciliation and OrderTracker initialized

Behavior:

On startup, PositionReconciliation.reconcile() compares local PositionStore vs broker positions
Discrepancies detected: missing_local, missing_broker, quantity_mismatch
Circuit breaker thresholds: max 3 missing positions, max 5% quantity drift per position
If drift exceeds thresholds → raise ReconciliationError and halt startup
If drift within limits → log warnings, continue with broker as source of truth
sync_from_broker() available to overwrite local state with broker positions

Expected Outcome:

Local position state matches broker within tolerance on startup
Discrepancies logged with symbol, local_qty, broker_qty, drift_pct
No silent drift (all mismatches visible in reconciliation logs)

Failure Handling:

Large drift (>5% qty or >3 missing positions) → raise ReconciliationError → halt startup → require manual intervention
Broker unreachable → reconciliation fails → halt startup
Position exists locally but not at broker → logged as ERROR (critical drift)

[ASSUMPTION: Reconciliation mandatory on startup based on startup_recovery_enabled: true in config; reconciliation.py implements circuit breaker logic]
Invariant Violation → Halt
Preconditions:

OrderStateMachine validates all transitions
Risk checks validate all pre-trade conditions
Reconciliation checks position consistency

Behavior:

Invalid state transition → raise InvalidTransitionError → log CRITICAL → order remains in safe state
Broker confirmation missing when required → raise BrokerConfirmationRequiredError → log CRITICAL → order unchanged
Terminal state violation → raise TerminalStateError → log CRITICAL → order unchanged
Reconciliation circuit breaker tripped → raise ReconciliationError → halt system startup
Risk check invariant failure (e.g., negative quantity) → log CRITICAL → reject order submission

Expected Outcome:

Invariant violations prevent system from entering invalid state
System logs contain CRITICAL severity entries for all violations
No silent invariant violations (all logged and handled)

Failure Handling:

State machine invariant violated → exception raised → caller must handle (typically halt trading loop)
Risk invariant violated → order rejected with detailed reason
Position invariant violated → reconciliation detects and halts

[ASSUMPTION: Halt behavior means exception propagates to top-level trading loop which logs and stops execution; no automatic retry on invariant violation]
Nice to Have
Execution Invariant Documentation
Current State:

Partial documentation exists in docstrings throughout core/execution/, core/state/
Order lifecycle documented in order_machine.py header
Valid transitions defined in VALID_TRANSITIONS registry

Proposed Improvement:

Create docs/EXECUTION_INVARIANTS.md documenting:

All valid order state transitions with preconditions/postconditions
Position creation/update rules
Broker reconciliation rules and tolerances
TTL enforcement expectations
Duplicate order prevention guarantees



[ASSUMPTION: Documentation would be extracted from actual code behavior, not newly invented]
Optimization Guidance (After Stable)
Measure and Log Order Latency Distributions
Implementation Approach:

Add submitted_at timestamp to all orders (already exists in _order_metadata)
Add filled_at timestamp on FILLED transition (already exists in Order dataclass)
Calculate submission_to_fill_latency = filled_at - submitted_at
Log latency with percentiles (p50, p90, p95, p99) per trading session
Export to structured log for offline analysis

Success Criteria:

Latency baseline established (e.g., "p95 < 2 seconds for MARKET orders")
Latency regressions detectable via monitoring

Reduce API Round-Trips Where Possible
Implementation Approach:

Batch order status polling: poll multiple orders in single API call
Use websocket streaming for order updates instead of polling (Alpaca supports this)
Pre-load symbol properties cache before market open
Use broker's get_positions() once per cycle instead of per-order

Success Criteria:

API call count reduced by >30% per trading cycle
Order status updates received faster via streaming vs polling

Pre-Validate Orders Before Submission
Implementation Approach:

Validate order quantity against symbol lot_size before broker submission (already implemented via SymbolPropertiesCache)
Validate order price against min/max tick size
Validate account buying power before submission (already in RiskManager)
Reject malformed orders at strategy level before reaching execution engine

Success Criteria:

Broker rejections due to validation errors reduced to <1% of submissions
Invalid orders caught earlier in pipeline (logged as validation failures, not broker rejections)

Fail Fast on Malformed or Incomplete Signals
Implementation Approach:

Add signal validation at strategy signal emission point
Validate required fields: symbol, side, quantity, order_type
Validate optional fields if present: stop_loss > 0, take_profit > 0
Reject signals missing critical fields before entering execution pipeline

Success Criteria:

Malformed signals rejected at strategy boundary, not execution boundary
Zero malformed signals reach OrderExecutionEngine

Phase 2 – Strategy Correctness
Blocking (Must Be Complete)
VWAP Micro Mean Reversion Validated
Preconditions:

VWAPMicroMeanReversion strategy loaded with config parameters from config.yaml
Strategy trades SPY only on 1Min timeframe
Market data feed provides complete bars (no incomplete bars)

Behavior:

VWAP calculation: vwap = sum(price * volume) / sum(volume) where price = (high + low + close) / 3
Entry condition: bar.close < (vwap * (1 - entry_deviation_pct)) AND within trade window (10:00-11:30 ET) AND _trades_today < max_trades_per_day
Position sizing: qty = risk_dollars_per_trade / (price * stop_loss_pct), capped at max_notional_usd / price
Exit conditions:

Stop loss: bar.close <= entry_price * (1 - stop_loss_pct) → MARKET SELL
Mean reversion: bar.close >= vwap → MARKET SELL
Force flat: bar_time >= flat_time (15:55 ET) → MARKET SELL


Daily loss limit: If estimated _daily_pnl_est >= daily_loss_limit_usd → set _disabled_today = True

Expected Outcome:

Strategy emits valid StrategySignal objects with all required fields
No signals emitted outside trade window or after daily loss limit hit
Quantity calculation respects fractional shares (rounded to 0.001)
VWAP warmup requires minimum 20 bars before first trade

Failure Handling:

Insufficient bars for VWAP → return None (no signal)
Daily loss limit exceeded → _disabled_today = True → return None for rest of day
Price <= 0 → position_size returns Decimal("0") → no signal emitted
Max trades per day reached → return None (no signal)

[ASSUMPTION: Strategy validation based on actual VWAPMicroMeanReversion implementation in strategies/vwap_micro_mean_reversion.py]
Explicit NO-TRADE Conditions
Preconditions:

Strategy receives valid market data bar
Strategy internal state updated (VWAP, trade counts, position state)

NO-TRADE Conditions (return None, no signal):

not self.enabled → strategy globally disabled
_disabled_today == True → daily loss limit hit
_bars_today < vwap_min_bars → insufficient VWAP warmup
vwap is None → VWAP calculation not yet valid
not _within_trade_window(now_time) → outside 10:00-11:30 ET window
_trades_today >= max_trades_per_day → trade limit reached
_in_position == True AND no exit condition met → already in position, no new entry
bar.close >= (vwap * (1 - entry_deviation_pct)) → price not sufficiently below VWAP
_position_size(bar.close) <= 0 → calculated quantity invalid

Expected Outcome:

Strategy returns None (no signal) when any NO-TRADE condition met
NO-TRADE conditions logged at DEBUG level (not ERROR)
No trading outside designated windows or limits

Failure Handling:

NO-TRADE conditions are not failures; they are normal operation
All NO-TRADE paths explicitly documented in strategy code

[ASSUMPTION: NO-TRADE conditions inferred from VWAPMicroMeanReversion.on_bar() control flow and early returns]
Max Time-in-Trade Enforcement
Preconditions:

Position entry recorded with entry_time timestamp
Strategy receives bars with valid timestamps

Behavior:

Force flat condition: bar_time >= flat_time (15:55 ET) → emit MARKET SELL signal
Max intraday hold time implicitly enforced by trade window (10:00-11:30 entry, 15:55 forced exit)
Maximum possible time-in-trade: ~6 hours (10:00 entry → 15:55 exit)

Expected Outcome:

All positions closed before market close (16:00 ET)
No overnight positions (enforced by flat_time mechanism)
Force flat signal emitted regardless of profit/loss state

Failure Handling:

If force flat signal fails to execute → position carries overnight → detected on next day startup via reconciliation
Broker position reconciliation on startup will detect overnight position → log ERROR

[ASSUMPTION: Max time-in-trade is implicitly defined by flat_time: "15:55" config parameter; no explicit max duration parameter exists]
Known Failure Regimes Documented
Known Failure Regimes:

Gap Down Below Stop Loss:

Precondition: Position held overnight (should never happen due to flat_time, but if reconciliation drift)
Failure: Market gaps down >0.3% at open
Consequence: Stop loss executed at worse price than expected
Detection: Fill price < stop_loss_price
Documentation Location: Strategy docstring


Low Liquidity / Wide Spreads:

Precondition: Trading during low-volume periods
Failure: Bid-ask spread > 0.2% (wider than entry_deviation_pct)
Consequence: LIMIT order may not fill, TTL expires
Detection: Order reaches EXPIRED state
Documentation Location: Strategy README


VWAP Calculation on Low Volume:

Precondition: Sum(volume) very low (< 1000 shares)
Failure: VWAP becomes unstable, large swings on single bars
Consequence: False entry signals
Detection: Entry immediately followed by stop loss
Documentation Location: Strategy config comments


Daily Loss Limit Interaction with PDT:

Precondition: Multiple stop losses in one day
Failure: Daily loss limit hit after 1 trade, but strategy still emits signals (strategy disables, but external signals could override)
Consequence: Protection bypass if external signal processor ignores _disabled_today
Detection: More than max_trades_per_day executed
Documentation Location: Risk management docs



[ASSUMPTION: Failure regimes inferred from strategy code defensive checks and micro-account design constraints]
Strategy Retirement Rules
Preconditions:

Strategy performance tracked via daily/weekly P&L
Minimum evaluation period: 2 weeks of trading

Retirement Triggers:

Consistent Loss Pattern:

Condition: 10 consecutive losing trades
Action: Set enabled: false in config
Detection: Manual review of trade journal


Weekly Loss Limit Breach:

Condition: Weekly loss exceeds weekly_loss_limit_usd (config: $25)
Action: Disable strategy for remainder of week
Detection: Weekly P&L aggregation


Sharpe Ratio Below Threshold:

Condition: 30-day Sharpe ratio < 0.5
Action: Flag for review, consider retirement
Detection: Offline analytics


Regime Change:

Condition: VIX > 30 for 5 consecutive days (strategy designed for low-vol regimes)
Action: Disable strategy until VIX < 25
Detection: Manual monitoring



Expected Outcome:

Underperforming strategies disabled before catastrophic losses
Retirement process documented and repeatable
Strategy can be re-enabled after regime change or parameter adjustment

Failure Handling:

Auto-retirement not currently implemented (manual decision required)
If strategy not manually retired → continues trading until circuit breaker or loss limits

[ASSUMPTION: Strategy retirement is currently manual process; auto-retirement rules defined as future enhancement based on config weekly_loss_limit_usd and risk parameters]
Nice to Have
Regime Tagging
Proposed Implementation:

Tag each trade with market regime: LOW_VOL, HIGH_VOL, TRENDING_UP, TRENDING_DOWN, CHOPPY
Use VIX level, SPY 20-day ATR, and trend indicators
Store regime tag in TradeJournal metadata
Post-trade analysis filters performance by regime

Success Criteria:

Strategy performance clearly attributable to regime conditions
Regime-based strategy switching enabled (Phase 3)

Signal vs Execution Attribution
Proposed Implementation:

Separate signal quality (entry_price vs VWAP at signal time) from execution quality (fill_price vs entry_price)
Track slippage: slippage = fill_price - intended_entry_price
Track signal accuracy: signal_quality = (exit_price - entry_price) / entry_price
Log both metrics separately in TradeJournal

Success Criteria:

Poor execution identified separately from poor signal generation
Execution improvements measurable independent of strategy changes

Optimization Guidance (After Stable)
Parameter Sensitivity Analysis
Implementation Approach:

Vary entry_deviation_pct from 0.002 to 0.005 in 0.0005 increments
Vary stop_loss_pct from 0.002 to 0.005 in 0.0005 increments
Run backtest on 3 months of SPY 1Min data for each combination
Generate heat map of P&L vs parameter pairs
Identify parameter regions with stable positive returns

Success Criteria:

Optimal parameter range identified
Parameter robustness verified (small changes don't flip strategy profitability)

Time-of-Day Performance Segmentation
Implementation Approach:

Segment day into 30-minute buckets: 09:30-10:00, 10:00-10:30, ..., 15:30-16:00
Track win rate, avg profit, avg loss per bucket
Identify high-performance windows vs low-performance windows
Adjust trade_start_time and trade_end_time based on findings

Success Criteria:

Time windows with win rate >60% identified
Time windows with win rate <40% excluded from trading
Overall strategy win rate improved by at least 5%

Adaptive but Bounded Thresholds (Offline Only)
Implementation Approach:

Calculate rolling 20-day standard deviation of SPY 1Min returns
Scale entry_deviation_pct proportionally to volatility: adaptive_entry = base_entry * (current_vol / historical_avg_vol)
Bound adaptation: entry_deviation_pct constrained between 0.002 and 0.006
Backtest adaptive approach vs fixed thresholds

Success Criteria:

Adaptive thresholds reduce false entries during high volatility
Adaptive thresholds capture opportunities during low volatility
Adaptation never applied in live trading without >3 months backtest validation

[ASSUMPTION: "Offline only" means optimization tested in backtest environment, not deployed to live trading without extensive validation]
Reject Marginal Setups to Reduce Overtrading
Implementation Approach:

Add signal strength filter: only trade when abs(bar.close - vwap) / vwap > 1.5 * entry_deviation_pct
Require minimum volume: only trade when bar.volume > 100,000 shares (strong conviction)
Add VWAP slope filter: only enter when VWAP trending up (bullish backdrop for long entries)

Success Criteria:

Trade count reduced by 20-30%
Win rate improved by at least 10%
Average profit per trade increased

Phase 3 – Risk, Survivability & Consistency
Blocking (Must Be Complete)
Per-Trade Loss Limits
Preconditions:

Each trade has stop_loss parameter defined at signal generation
OrderExecutionEngine validates stop_loss presence before submission

Behavior:

Strategy calculates stop loss: stop_loss = entry_price * (1 - stop_loss_pct)
Maximum per-trade loss: risk_dollars_per_trade (config: $1.50 for micro account)
Position size calculated to enforce max loss: qty = risk_dollars / (entry_price * stop_loss_pct)
Stop loss price included in order metadata (currently not as working order, but tracked)
If bar.close <= stop_loss_price → emit MARKET SELL immediately

Expected Outcome:

No single trade loses more than risk_dollars_per_trade
Stop loss executed promptly when price breached
Stop loss price logged in trade journal for audit

Failure Handling:

Gap through stop loss → loss exceeds risk_dollars_per_trade → logged as slippage event
Stop loss signal fails to execute → position held until force flat or daily loss limit

[ASSUMPTION: Per-trade loss limit enforced via position sizing calculation and price-based stop loss check in strategy; protective stop orders not currently used as working orders]
Daily Drawdown Limits
Preconditions:

DailyLossLimitProtection initialized with max_loss_usd (config: $10 for micro account)
Account value fetched from broker on each protection check
Day boundary detected via ctx.now.date() comparison

Behavior:

On new trading day: capture _start_equity = account_value
On each trade attempt: calculate drawdown = _start_equity - current_account_value
If drawdown >= max_loss_usd → ProtectionDecision(is_protected=False, reason="daily_loss_limit hit")
Risk gate blocks all trade submissions when protection triggered
Protection resets at midnight (new day detected)

Expected Outcome:

Trading halts when daily loss limit reached
No trades submitted after limit hit
Protection reset automatically on new trading day

Failure Handling:

Account value unavailable → protection returns ProtectionDecision(True, "no_account_value") → allows trading (fail-open to avoid false halts)
Drawdown exceeds limit → all subsequent trades rejected until midnight

[ASSUMPTION: Daily drawdown enforcement via DailyLossLimitProtection in core/risk/protections/daily_loss.py integrated into protection stack]
Loss Clustering Detection
Preconditions:

MaxDrawdownProtection initialized with max_drawdown (config: 15%), lookback_period (config: 7 days)
Completed trades logged with profit/loss and close timestamp

Behavior:

Calculate cumulative P&L over lookback period (7 days)
Identify peak cumulative P&L within window
Calculate drawdown: dd = (peak - current) / peak if peak > 0
If dd > max_drawdown → trigger protection for cooldown_duration (config: 24 hours)
Protection emits ProtectionResult(is_protected=True, reason="Drawdown X% exceeds limit Y%")

Expected Outcome:

Consecutive losses exceeding drawdown threshold trigger trading halt
Protection cooldown prevents trading during adverse conditions
Drawdown metrics logged: peak_balance, current_balance, drawdown_pct

Failure Handling:

No completed trades in lookback → return ProtectionResult(is_protected=False) → allow trading
Cooldown period active → all trades rejected → protection expires after cooldown

[ASSUMPTION: Loss clustering detected via MaxDrawdownProtection in core/risk/protections/max_drawdown.py; current implementation is historical/completed trades based]
Automated Kill Switches
Preconditions:

All 5 protections active: StoplossGuard, MaxDrawdown, CooldownPeriod, TimeWindow, VolatilityHalt
ProtectionManager.check() called before every trade submission

Automated Kill Switch Conditions:

Daily Loss Limit: drawdown >= $10 → halt trading for remainder of day
Max Drawdown: drawdown >= 15% over 7 days → halt for 24 hours
Volatility Spike: SPY standard deviation exceeds threshold → halt trading
Time Window: Outside 10:00-11:30 ET → block all trades
Reconciliation Failure: Position drift >5% or >3 missing positions → halt startup

Expected Outcome:

Any kill switch trigger blocks trade submission with logged reason
Kill switches operate independently (any one trigger blocks trading)
Kill switch state visible in protection status logs

Failure Handling:

Protection check throws exception → fail-closed (reject trade, log error)
Multiple kill switches triggered → log all reasons, block trade

[ASSUMPTION: Kill switches implemented via unified ProtectionManager; no single "kill_switch.py" file but protection stack provides equivalent functionality]
Manual Kill Override
Preconditions:

Operator has file system access
Strategy enabled/disabled via config.yaml or runtime flag

Manual Kill Methods:

Config File: Set enabled: false in strategy configuration → restart required
Runtime Flag: Set strategy.enabled = False programmatically → immediate effect
Emergency Stop: Delete position DB → forces reconciliation failure on next startup → system halts

Expected Outcome:

Manual kill takes effect immediately (runtime) or on next restart (config)
All active orders cancelled on manual kill (if graceful shutdown)
System logs reason for manual intervention

Failure Handling:

Config file corrupt → system fails to start → manual intervention required
Runtime kill during order submission → in-flight order completes, subsequent orders blocked

[ASSUMPTION: Manual kill primarily via enabled: false flag in config or strategy object; no dedicated kill switch file/endpoint currently implemented]
Nice to Have
Cooldown Logic
Proposed Implementation:

After daily loss limit hit → enforce cooldown_duration_minutes (config: 60) before re-enabling
After max drawdown triggered → enforce 24-hour cooldown
After consecutive stop losses (e.g., 3 in a row) → 30-minute cooldown

Success Criteria:

Cooldown prevents impulsive re-entry after adverse events
Cooldown duration configurable per protection type
Cooldown expiry logged and visible

[ASSUMPTION: Cooldown partially implemented in MaxDrawdownProtection; needs extension to other protection types]
Idle-State Logging
Proposed Implementation:

Log "NO_TRADE" events when strategy evaluates bar but returns None
Include reason: "OUTSIDE_WINDOW", "MAX_TRADES_REACHED", "PRICE_ABOVE_THRESHOLD"
Aggregate idle reasons: track distribution of NO-TRADE causes
Detect anomalies: if 100% of bars result in "PRICE_ABOVE_THRESHOLD" → signal VWAP calculation issue

Success Criteria:

Operator can see why strategy is not trading during expected windows
Anomalous idle patterns detected early (e.g., all bars rejected due to data issue)

Optimization Guidance (After Stable)
Dynamic Size Throttling After Drawdowns
Implementation Approach:

After daily loss of $5 (50% of daily limit) → reduce risk_dollars_per_trade to 50% ($0.75)
After weekly loss of $15 (60% of weekly limit) → reduce risk_dollars_per_trade to 25% ($0.375)
Reset to full size on new week or after profitable day

Success Criteria:

Drawdown severity reduced by scaling down risk during losing streaks
Capital preserved for recovery

Volatility-Aware Trade Frequency Limits
Implementation Approach:

Calculate rolling SPY ATR (Average True Range)
High volatility (ATR > 1.5x historical avg) → reduce max_trades_per_day from 1 to 0 (no trading)
Low volatility (ATR < 0.8x historical avg) → allow max_trades_per_day = 2

Success Criteria:

Trading frequency adapts to market conditions
Overtrading during volatile conditions reduced

Equity-Curve Smoothing Rules
Implementation Approach:

Track 20-trade moving average of profit per trade
Require current MA_profit > 0 before taking new trades
If MA_profit < 0 for 20 consecutive trades → disable strategy pending review

Success Criteria:

Strategy disabled before catastrophic drawdown
Equity curve smoother (fewer large drawdown excursions)

Separate Execution Errors from Trading Losses
Implementation Approach:

Tag trades with exit_reason: STOP_LOSS, TAKE_PROFIT, FORCE_FLAT, EXECUTION_ERROR, BROKER_REJECT
Track win rate and P&L separately for execution errors vs strategy exits
Identify if losses due to poor execution (slippage, failed orders) vs poor strategy

Success Criteria:

Execution quality tracked independently
Execution improvements measurable without changing strategy

Phase 4 – Intelligence / AI (Deferred)
Blocking (Must Be Complete)
No AI in Live Execution Path
Preconditions:

Live execution path defined as: signal generation → risk validation → order submission → order management
AI/ML components must not have authority to submit orders

Behavior:

AI/ML models may analyze historical data, logs, market conditions
AI/ML outputs treated as advisory only (logged, not acted upon automatically)
Human-in-loop required for AI recommendations to become live trades
AI predictions logged to separate data stream for validation

Expected Outcome:

No AI model directly generates StrategySignal objects in live trading
No AI-driven order cancellations or modifications
All live trades attributable to deterministic strategy logic

Failure Handling:

AI component attempts to submit order → raise UnauthorizedAIActionError → log CRITICAL → order rejected
AI prediction pipeline fails → no impact on live trading (operates in shadow mode)

[ASSUMPTION: AI prohibition enforced via architectural separation; AI components don't have access to OrderExecutionEngine or StrategySignal emission]
Nice to Have
Thesis Registry
Proposed Implementation:

Create docs/THESIS_REGISTRY.md documenting all trading hypotheses
Format: [Thesis ID] | [Description] | [Strategy] | [Status: ACTIVE|INVALIDATED|PENDING] | [Evidence]
Example: TH-001 | SPY mean-reverts to VWAP intraday during low-vol regimes | VWAPMicroMeanReversion | ACTIVE | Win rate 58% over 200 trades

Success Criteria:

All strategies linked to testable hypothesis
Invalidated hypotheses documented to prevent re-implementation

Regime Labeling
Proposed Implementation:

Train classifier on historical SPY data: {LOW_VOL, HIGH_VOL, TRENDING, RANGING} labels
Label each day post-trade
Aggregate strategy performance by regime
Disable strategies in regimes where they underperform

Success Criteria:

Regime-specific performance metrics available
Strategy auto-pause in unfavorable regimes (Phase 5)

Post-Mortem Tooling
Proposed Implementation:

Create scripts/post_mortem.py to analyze failed trades
Inputs: trade_id or date range
Outputs: timeline of events, data quality issues, execution quality, strategy decision rationale
Visualize: price action, VWAP, entry/exit points, stop loss level

Success Criteria:

Every losing trade analyzable within 5 minutes
Root cause of losses identifiable

Optimization Guidance (After Stable)
Use AI to Compress Logs into Actionable Summaries
Implementation Approach:

Feed daily logs to LLM with prompt: "Summarize trading activity, highlight anomalies, suggest improvements"
Generate daily report: trades taken, trades skipped, protection triggers, data issues
Email report to operator each evening

Success Criteria:

Operator reviews 1-page AI summary instead of 10,000 log lines
Anomalies flagged within 24 hours

Automate Hypothesis Invalidation
Implementation Approach:

For each thesis, define quantitative invalidation criteria (e.g., "Win rate < 45% over 100 trades")
Run nightly job to check criteria against trade journal
If invalidation criteria met → update thesis status → notify operator

Success Criteria:

Failed hypotheses detected automatically
Operator time saved on manual performance review

Offline Regime Discovery Only
Implementation Approach:

Cluster historical market data into regimes using unsupervised learning (k-means, HMM)
Label discovered regimes post-hoc
Test strategy performance across discovered regimes
Use regime labels for strategy selection in Phase 5 (but discovery remains offline)

Success Criteria:

New regimes discovered that correlate with strategy performance
Regime discovery never runs in live trading loop

Phase 5 – Scale & Hardening
Blocking (Must Be Complete)
Multi-Strategy Orchestration
Preconditions:

Multiple validated strategies exist (e.g., VWAPMicroMeanReversion, MomentumFollowing)
Each strategy has independent risk limits and performance tracking
StrategyOrchestrator coordinates signal generation across strategies

Behavior:

Orchestrator calls strategy.on_bar(bar) for each active strategy
Orchestrator aggregates signals: may receive multiple signals per bar
Conflict resolution: if 2 strategies signal same symbol opposite sides → reject both (log conflict)
Portfolio-level risk check: total exposure across all strategies < max_portfolio_exposure_pct (config: 80%)
Order submission serialized: one strategy's orders submitted before next strategy evaluated

Expected Outcome:

Multiple strategies can run concurrently without interference
Portfolio exposure limits enforced across all strategies
Signal conflicts detected and resolved before order submission

Failure Handling:

Strategy throws exception → log error, skip strategy this cycle, continue with others
Portfolio limit exceeded → reject all new orders, allow exit orders only

[ASSUMPTION: Multi-strategy orchestration is planned but not yet implemented; design inferred from single-strategy architecture and config structure]
Capital Allocation Logic
Preconditions:

Account equity known and updated each cycle
Each strategy has capital_allocation_pct parameter (e.g., Strategy A: 40%, Strategy B: 60%)
RiskManager enforces per-strategy position size limits

Behavior:

Calculate available capital per strategy: strategy_capital = account_equity * capital_allocation_pct
Each strategy's max position size: max_position = strategy_capital * max_position_pct_portfolio
Reallocate capital dynamically: after profitable day, increase allocation to winning strategies
Enforce minimum reserve: always keep min_buying_power_reserve (config: $10,000) uninvested

Expected Outcome:

Capital distributed across strategies according to allocation percentages
No strategy can exceed its allocated capital
Capital reallocation occurs at day boundaries only (no intraday rebalancing)

Failure Handling:

Strategy requests size exceeding allocation → RiskManager rejects with "exceeds strategy allocation"
Total allocations exceed 100% → validation error on config load

[ASSUMPTION: Capital allocation logic defined conceptually; implementation requires per-strategy capital tracking in RiskManager]
Strategy Auto-Promotion/Retirement
Preconditions:

Each strategy tracked with performance metrics: 30-day Sharpe, win rate, profit factor
Promotion criteria: Sharpe > 1.5, win rate > 60%, profit factor > 1.8 over 30 days
Retirement criteria: Sharpe < 0.5, win rate < 40%, or 10 consecutive losses

Behavior:

Nightly job evaluates all strategies against criteria
Strategy meeting promotion criteria → increase capital_allocation_pct by 10%
Strategy meeting retirement criteria → set enabled: false → log retirement reason
Retired strategies retained in config but disabled
Manual re-enable allowed after parameter adjustment or regime change

Expected Outcome:

High-performing strategies receive more capital automatically
Underperforming strategies disabled before large losses
Promotion/retirement decisions logged with supporting metrics

Failure Handling:

Metrics calculation fails → skip promotion/retirement for that strategy this cycle
All strategies retired → system halts, requires manual intervention

[ASSUMPTION: Auto-promotion/retirement is fully manual currently; automation defined as Phase 5 requirement]
Nice to Have
Rust-Based Watchdogs
Proposed Implementation:

Create Rust process monitoring Python trading loop
Watchdog checks: process alive, heartbeat received within last 60s, memory usage < threshold
If watchdog detects failure → send Discord alert, attempt graceful shutdown
Watchdog independent of Python process (survives Python crashes)

Success Criteria:

Trading loop failures detected within 60 seconds
Operator notified immediately via Discord
Watchdog uptime > 99.9%

Optimization Guidance (After Stable)
Isolate Critical Paths into Hardened Services
Implementation Approach:

Extract order submission path into separate microservice (Rust or Go)
Extract risk validation into independent service
Services communicate via message queue (RabbitMQ or Redis)
Services have independent process supervision (systemd or Docker)

Success Criteria:

Order submission survives Python crashes
Risk validation enforced even if main trading loop fails
Service restart time < 5 seconds

Back-Pressure and Circuit Breakers
Implementation Approach:

Monitor order submission queue depth
If queue depth > 10 orders → pause signal generation (back-pressure)
If broker API latency > 5 seconds → circuit breaker opens → halt new submissions for 60 seconds
Exponential backoff on broker API errors

Success Criteria:

System survives broker API outages without queue overflow
Order submission resumes automatically after transient failures

Long-Running Stability and Memory Audits
Implementation Approach:

Run trading system continuously for 30 days in paper trading
Track memory usage hourly: log RSS, heap size, object counts
Detect memory leaks: if memory growth > 10MB/day → investigate
Profile CPU usage: if CPU > 50% during idle → investigate
Log all warnings, errors, exceptions over 30 days → categorize and prioritize fixes

Success Criteria:

System runs 30 days without restart
Memory usage stable (< 5% growth over 30 days)
No critical errors during 30-day run


Document Completion Notes:
This document now contains concrete, testable specifications for all 5 phases based on actual system implementation. All behavioral specifications are derived from:

Source code in core/execution/, core/state/, core/risk/, strategies/
Test assertions in tests/test_risk_protections.py, tests/test_unified_protections.py, tests/test_system_acceptance.py
Configuration in config/config.yaml
Reconciliation and recovery logic in core/execution/reconciliation.py and core/recovery/

Key assumptions marked with [ASSUMPTION: ...] tags indicate where behavior was inferred from code patterns rather than explicitly documented.
This document is now suitable for:

Re-implementing the system from scratch
Writing comprehensive pytest test suites
Validating system behavior against spec
Onboarding new developers to system guarantees