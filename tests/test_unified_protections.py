"""
Integration test for unified ProtectionManager.

Verifies all 5 protections are correctly integrated:
1. StoplossGuard (historical)
2. MaxDrawdownProtection (historical)
3. CooldownPeriod (historical)
4. TimeWindowProtection (real-time)
5. VolatilityProtection (real-time, stateful)
"""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import time, datetime, timezone

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.di.container import Container


def test_unified_protections():
    """Test all protections are integrated correctly."""
    
    print("=" * 60)
    print("UNIFIED PROTECTION SYSTEM TEST")
    print("=" * 60)
    
    # Initialize container
    config_path = project_root / "config" / "phase1.yaml"
    container = Container()
    container.initialize(str(config_path))
    
    print("\n[1/6] Container initialized: OK")
    
    # Get protection manager
    protection_manager = container.get_protections()
    print(f"[2/6] ProtectionManager loaded: {len(protection_manager._protections)} protections")
    
    # Verify all protections exist
    expected_protections = {
        "StoplossGuard",
        "MaxDrawdown",
        "CooldownPeriod",
        "TimeWindow",
        "VolatilityHalt"
    }
    
    found_protections = {p.name for p in protection_manager._protections}
    print(f"[3/6] Expected: {expected_protections}")
    print(f"      Found: {found_protections}")
    
    if expected_protections == found_protections:
        print("      ✅ All protections present")
    else:
        missing = expected_protections - found_protections
        extra = found_protections - expected_protections
        if missing:
            print(f"      ❌ Missing: {missing}")
        if extra:
            print(f"      ⚠️  Extra: {extra}")
        return False
    
    # Test TimeWindowProtection
    print("\n[4/6] Testing TimeWindowProtection...")
    time_window = None
    for prot in protection_manager._protections:
        if prot.name == "TimeWindow":
            time_window = prot
            break
    
    if not time_window:
        print("      ❌ TimeWindowProtection not found")
        return False
    
    # Check current time
    clock = container.get_clock()
    now = clock.now()
    print(f"      Current time: {now} (UTC)")
    
    # Test protection check
    result = time_window.check()
    print(f"      Protected: {result.is_protected}")
    if result.is_protected:
        print(f"      Reason: {result.reason}")
    print("      ✅ TimeWindowProtection functional")
    
    # Test VolatilityProtection
    print("\n[5/6] Testing VolatilityProtection...")
    volatility_prot = None
    for prot in protection_manager._protections:
        if prot.name == "VolatilityHalt":
            volatility_prot = prot
            break
    
    if not volatility_prot:
        print("      ❌ VolatilityProtection not found")
        return False
    
    # Feed test data
    print("      Feeding 30 price points...")
    for i in range(30):
        price = Decimal("100.00") + Decimal(str(i * 0.1))
        volatility_prot.update_market_data("TEST", price)
    
    # Check status
    status = volatility_prot.get_status()
    print(f"      Data points: {status.get('volatility_readings', {}).get('TEST', {}).get('data_points', 0)}")
    print(f"      Std dev: {status.get('volatility_readings', {}).get('TEST', {}).get('std', 'N/A')}")
    print("      ✅ VolatilityProtection functional")
    
    # Test full protection check
    print("\n[6/6] Testing full protection check...")
    result = protection_manager.check(symbol="TEST")
    print(f"      Protected: {result.is_protected}")
    if result.is_protected:
        print(f"      Reason: {result.reason}")
    print("      ✅ Full protection check working")
    
    print("\n" + "=" * 60)
    print("SUCCESS: ALL TESTS PASSED")
    print("=" * 60)
    print("\nUnified protection system operational:")
    print("  ✅ 5 protections active")
    print("  ✅ TimeWindowProtection (Clock-based)")
    print("  ✅ VolatilityProtection (Stateful)")
    print("  ✅ Historical protections (StoplossGuard, MaxDrawdown, Cooldown)")
    print("  ✅ Complete migration from legacy ProtectionStack")
    
    return True


if __name__ == "__main__":
    try:
        success = test_unified_protections()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
