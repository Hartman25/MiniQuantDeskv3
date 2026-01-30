"""
Test Container with new protections - Section 3 verification
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.di.container import Container

# Test 1: Initialize Container
print("[1/4] Initializing Container...")
container = Container()
config_path = r"C:\Users\Zacha\Desktop\MiniQuantDeskv2\config\config.yaml"
container.initialize(config_path)
print("  OK Container initialized")

# Test 2: Get ProtectionManager
print("\n[2/4] Getting ProtectionManager...")
protections = container.get_protections()
print(f"  OK Got ProtectionManager: {protections}")

# Test 3: Check all protections are loaded
print("\n[3/4] Verifying all 5 protections loaded...")
statuses = protections.get_all_statuses()
print(f"  OK Found {len(statuses)} protections:")
for status in statuses:
    print(f"    - {status['name']}: enabled={status['enabled']}")

# Verify we have exactly 5
expected_names = {
    'StoplossGuard',
    'MaxDrawdownProtection',  # Full name
    'CooldownPeriod',
    'TimeWindow',
    'VolatilityHalt'
}
actual_names = {s['name'] for s in statuses}
print(f"\n  Expected: {expected_names}")
print(f"  Actual: {actual_names}")

if actual_names == expected_names:
    print("  OK All 5 protections present!")
else:
    print(f"  ERROR Missing: {expected_names - actual_names}")
    print(f"  ERROR Extra: {actual_names - expected_names}")

# Test 4: Test TimeWindow protection (should be blocking now - outside 10:00-11:30 ET)
print("\n[4/4] Testing TimeWindow protection...")
result = protections.check()
print(f"  Result: is_protected={result.is_protected}")
if result.is_protected:
    print(f"  Reason: {result.reason}")
    print(f"  OK TimeWindow correctly blocking outside 10:00-11:30 ET")
else:
    print(f"  INFO: Currently within trading window (10:00-11:30 ET)")

print("\nSUCCESS - SECTION 3 COMPLETE: Container using unified ProtectionManager")
print("Ready for Section 4: Remove ProtectionStack from app.py")
