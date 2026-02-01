MiniQuantDesk – Phase-Gated Checklist with Optimization Guidance
Phase 1 – Execution Integrity
Blocking (Must Be Complete)
Deterministic order lifecycle
Single active position enforcement
TTL / cancellation correctness
Idempotent event handling
Restart reconciliation with broker
Invariant violation → halt
Nice to Have
Execution invariant documentation
Optimization Guidance (After Stable)
Measure and log order latency distributions
Reduce API round-trips where possible
Pre-validate orders before submission
Fail fast on malformed or incomplete signals
Phase 2 – Strategy Correctness
Blocking (Must Be Complete)
VWAP Micro Mean Reversion validated
Explicit NO-TRADE conditions
Max time-in-trade enforcement
Known failure regimes documented
Strategy retirement rules
Nice to Have
Regime tagging
Signal vs execution attribution
Optimization Guidance (After Stable)
Parameter sensitivity analysis
Time-of-day performance segmentation
Adaptive but bounded thresholds (offline only)
Reject marginal setups to reduce overtrading
Phase 3 – Risk, Survivability & Consistency
Blocking (Must Be Complete)
Per-trade loss limits
Daily drawdown limits
Loss clustering detection
Automated kill switches
Manual kill override
Nice to Have
Cooldown logic
Idle-state logging
Optimization Guidance (After Stable)
Dynamic size throttling after drawdowns
Volatility-aware trade frequency limits
Equity-curve smoothing rules
Separate execution errors from trading losses
Phase 4 – Intelligence / AI (Deferred)
Blocking (Must Be Complete)
No AI in live execution path
Nice to Have
Thesis registry
Regime labeling
Post-mortem tooling
Optimization Guidance (After Stable)
Use AI to compress logs into actionable summaries
Automate hypothesis invalidation
Offline regime discovery only
Phase 5 – Scale & Hardening
Blocking (Must Be Complete)
Multi-strategy orchestration
Capital allocation logic
Strategy auto-promotion/retirement
Nice to Have
Rust-based watchdogs
Optimization Guidance (After Stable)
Isolate critical paths into hardened services
Back-pressure and circuit breakers
Long-running stability and memory audits
