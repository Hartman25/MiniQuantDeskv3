"""
TEST 1: Import Validation
Verifies all new components can be imported without errors.
"""

import sys
from pathlib import Path

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

print("="*70)
print("TEST 1: IMPORT VALIDATION")
print("="*70)
print()

failures = []

# Test imports
tests = [
    ('Data Contract', 'core.data.contract', 'MarketDataContract'),
    ('Data Validator', 'core.data.validator', 'DataValidator'),
    ('Data Cache', 'core.data.cache', 'DataCache'),
    ('Event Types', 'core.events.types', 'OrderFilledEvent'),
    ('Event Factory', 'core.events.types', 'EventFactory'),
    ('Event Handlers', 'core.events.handlers', 'EventHandlerRegistry'),
    ('Risk Limits', 'core.risk.limits', 'PersistentLimitsTracker'),
    ('Position Sizer', 'core.risk.sizing', 'NotionalPositionSizer'),
    ('Risk Gate', 'core.risk.gate', 'PreTradeRiskGate'),
    ('Reconciler', 'core.state.reconciler', 'BrokerReconciler'),
    ('Strategy Interface', 'strategies.base', 'IStrategy'),
    ('Strategy Registry', 'strategies.registry', 'StrategyRegistry'),
    ('Strategy Lifecycle', 'strategies.lifecycle', 'StrategyLifecycleManager'),
    ('VWAP Strategy', 'strategies.vwap_mean_reversion', 'VWAPMeanReversion'),
    ('DI Container', 'core.di.container', 'Container'),
]

for name, module_name, class_name in tests:
    try:
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
        print(f"[PASS] {name}")
    except Exception as e:
        failures.append((name, str(e)))
        print(f"[FAIL] {name}: {e}")

print()
print("="*70)
print(f"RESULT: {len(tests) - len(failures)}/{len(tests)} passed")
print("="*70)

if failures:
    print("\nFAILURES:")
    for name, error in failures:
        print(f"  - {name}: {error}")
    sys.exit(1)
else:
    print("\n[SUCCESS] All imports working")
    sys.exit(0)
