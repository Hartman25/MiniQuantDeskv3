# LEGACY PROTECTIONS SYSTEM

**STATUS: DEPRECATED - DO NOT USE**

This directory contains the old protection system that has been replaced by the unified `core.risk.protections` package.

## Migration Complete

All production code now uses:
- `core.risk.protections.ProtectionManager` (replaces ProtectionStack)
- `core.risk.protections.TimeWindowProtection` (replaces TradingWindowProtection)
- `core.risk.protections.VolatilityProtection` (replaces VolatilityHaltProtection)

## Why This Directory Still Exists

Kept for historical reference only. These files are no longer imported by any production code.

### Old System Components:
- `base.py` - IProtection protocol, ProtectionContext, ProtectionDecision
- `stack.py` - ProtectionStack orchestrator
- `daily_loss.py` - DailyLossLimitProtection (now handled by RiskGate)
- `max_trades.py` - MaxTradesPerDayProtection (now handled by RiskGate)
- `time_window.py` - TradingWindowProtection (replaced by new TimeWindowProtection)
- `volatility_halt.py` - VolatilityHaltProtection (replaced by new VolatilityProtection)

## New System Location

All active protection code is in: `core/risk/protections/`

Contains:
- `manager.py` - ProtectionManager (unified orchestrator)
- `base.py` - Protection base classes (GlobalProtection, SymbolProtection)
- `time_window.py` - NEW TimeWindowProtection (GlobalProtection)
- `volatility.py` - NEW VolatilityProtection (GlobalProtection)
- `stoploss_guard.py` - StoplossGuard (SymbolProtection)
- `max_drawdown.py` - MaxDrawdownProtection (GlobalProtection)
- `cooldown.py` - CooldownPeriod (GlobalProtection)

## Migration Date

January 24, 2026

## Safe to Delete?

**NO - Keep for reference.**

This code contains the original implementation logic that was used to build the new system. May be useful for:
- Understanding original design decisions
- Verifying migration correctness
- Rolling back if critical issues discovered

## Verification

To verify no production code uses this:

```bash
# Should return ONLY test/verification scripts
grep -r "protections_old" . --include="*.py" | grep -v test | grep -v __pycache__
```

Expected: No results (or only VERIFY_PROTECTION_MIGRATION.py)
