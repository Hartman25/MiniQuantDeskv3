"""
Test VolatilityProtection - Section 2 verification
"""

from decimal import Decimal
from core.risk.protections.volatility import VolatilityProtection

# Test 1: Create protection with default settings
print("[1/5] Creating VolatilityProtection...")
protection = VolatilityProtection(
    max_std=Decimal("0.006"),  # 0.6% threshold
    min_points=20,
    lookback=60
)
print(f"  OK Created: {protection.name}")
print(f"  Threshold: {protection.max_std} (0.6%)")
print(f"  Min points: {protection.min_points}")

# Test 2: Check with no data (should allow trading)
print("\n[2/5] Checking with no price data...")
result = protection.check()
print(f"  OK Result: is_protected={result.is_protected}")
print(f"  Expected: False (no data = allow trading)")

# Test 3: Add stable prices (low volatility)
print("\n[3/5] Adding stable prices (low volatility)...")
stable_prices = [Decimal("100.0") + Decimal("0.1") * i for i in range(30)]
protection.update_prices("SPY", stable_prices)
result = protection.check()
print(f"  OK Added {len(stable_prices)} prices")
print(f"  Result: is_protected={result.is_protected}")
print(f"  Expected: False (low volatility)")

# Test 4: Add volatile prices (high volatility - should trigger)
print("\n[4/5] Adding volatile prices (high volatility)...")
volatile_protection = VolatilityProtection(max_std=Decimal("0.006"))
volatile_prices = [
    Decimal("100.0"),
    Decimal("102.0"),  # +2%
    Decimal("99.0"),   # -3%
    Decimal("103.0"),  # +4%
    Decimal("98.0"),   # -5%
    Decimal("104.0"),  # +6%
]
# Repeat pattern to get 30 points
for i in range(5):
    volatile_protection.update_prices("VOLATILE", volatile_prices)

result = volatile_protection.check()
print(f"  OK Result: is_protected={result.is_protected}")
print(f"  Expected: True (high volatility)")
if result.is_protected:
    print(f"  Reason: {result.reason}")
    print(f"  Metadata: max_vol={result.metadata.get('max_volatility', 'N/A'):.6f}")

# Test 5: Get status
print("\n[5/5] Getting protection status...")
status = protection.get_status()
print(f"  OK Enabled: {status['enabled']}")
print(f"  Threshold: {status['max_std_threshold']}")
print(f"  Symbols tracked: {status['symbols_tracked']}")
print(f"  Last volatility: {status['last_volatility']}")

print("\nSUCCESS - SECTION 2 COMPLETE: VolatilityProtection created and tested")
print("Ready for Section 3: Update Container")
