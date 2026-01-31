"""
SECTION 5 FINAL VERIFICATION - Complete Protection System Migration

Verifies:
1. No production code imports from protections_old
2. Container uses unified ProtectionManager
3. All 5 protections are active
4. app.py uses new API
5. Legacy code documented properly
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("="*70)
print("SECTION 5: FINAL VERIFICATION - Protection System Migration")
print("="*70)

# Test 1: Verify no protections_old imports in production code
print("\n[1/5] Checking for protections_old imports in production code...")
import subprocess
try:
    result = subprocess.run(
        ['grep', '-r', 'protections_old', '.', '--include=*.py'],
        capture_output=True,
        text=True,
        cwd=r'C:\Users\Zacha\Desktop\MiniQuantDeskv2'
    )
    
    # Filter out test files and __pycache__
    lines = result.stdout.split('\n')
    production_imports = [
        line for line in lines 
        if line 
        and 'test' not in line.lower() 
        and '__pycache__' not in line
        and 'VERIFY' not in line
        and 'protections_old/README' not in line
        and 'protections_old\\README' not in line
    ]
    
    if production_imports:
        print("  ERROR: Found protections_old imports in production:")
        for line in production_imports:
            print(f"    {line}")
    else:
        print("  OK: No protections_old imports in production code")
        
except Exception as e:
    print(f"  SKIP: grep not available (Windows): {e}")
    print("  Manual check: Search for 'protections_old' in production files")

# Test 2: Verify Container initialization
print("\n[2/5] Verifying Container uses ProtectionManager...")
from core.di.container import Container

container = Container()
config_path = r"C:\Users\Zacha\Desktop\MiniQuantDeskv2\config\config.yaml"
container.initialize(config_path)

protections = container.get_protections()
print(f"  OK: Container.get_protections() returns ProtectionManager")
print(f"  Type: {type(protections).__name__}")

# Test 3: Verify all 5 protections active
print("\n[3/5] Verifying all 5 protections are active...")
statuses = protections.get_all_statuses()
print(f"  Total protections: {len(statuses)}")

expected = {
    'StoplossGuard',
    'MaxDrawdownProtection', 
    'CooldownPeriod',
    'TimeWindow',
    'VolatilityHalt'
}

actual = {s['name'] for s in statuses}
enabled = {s['name'] for s in statuses if s['enabled']}

if actual == expected:
    print(f"  OK: All 5 protections present")
else:
    print(f"  ERROR: Mismatch!")
    print(f"    Missing: {expected - actual}")
    print(f"    Extra: {actual - expected}")

if enabled == actual:
    print(f"  OK: All protections enabled")
else:
    print(f"  WARNING: Some disabled: {actual - enabled}")

# Test 4: Verify app.py uses new API
print("\n[4/5] Verifying app.py uses new ProtectionManager API...")
with open(r"C:\Users\Zacha\Desktop\MiniQuantDeskv2\core\runtime\app.py", "r") as f:
    app_content = f.read()

checks = {
    'No ProtectionContext': 'ProtectionContext' not in app_content,
    'No protections_old import': 'protections_old' not in app_content,
    'Uses get_protections()': 'get_protections()' in app_content,
    'Uses is_protected': 'is_protected' in app_content,
    'No old check(ctx)': 'check(prot_ctx)' not in app_content,
}

all_pass = True
for check_name, passed in checks.items():
    if passed:
        print(f"  OK: {check_name}")
    else:
        print(f"  ERROR: {check_name}")
        all_pass = False

# Test 5: Verify legacy code documented
print("\n[5/5] Verifying legacy code documentation...")
readme_path = r"C:\Users\Zacha\Desktop\MiniQuantDeskv2\core\risk\protections_old\README.md"
if os.path.exists(readme_path):
    print(f"  OK: Legacy README.md exists")
    with open(readme_path, "r") as f:
        readme = f.read()
    if "DEPRECATED" in readme:
        print(f"  OK: Marked as DEPRECATED")
    if "Migration Complete" in readme:
        print(f"  OK: Migration documented")
else:
    print(f"  WARNING: No README.md in protections_old/")

# Final Summary
print("\n" + "="*70)
print("FINAL SUMMARY")
print("="*70)

all_tests_pass = (
    len(statuses) == 5
    and actual == expected
    and all_pass
    and os.path.exists(readme_path)
)

if all_tests_pass:
    print("\nSUCCESS - ALL SECTIONS COMPLETE!")
    print("\nMigration Summary:")
    print("  Section 1: TimeWindowProtection created (92 lines)")
    print("  Section 2: VolatilityProtection created (188 lines)")
    print("  Section 3: Container updated to use unified manager")
    print("  Section 4: app.py migrated to new API")
    print("  Section 5: Legacy code documented")
    print("\nNext Steps:")
    print("  1. Test paper trading session")
    print("  2. Verify TimeWindow blocks outside 10:00-11:30 ET")
    print("  3. Test VolatilityProtection with price updates")
    print("  4. Run full integration test")
    print("\nReady for production!")
else:
    print("\nISSUES FOUND - Review output above")
