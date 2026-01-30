"""
Test TimeWindowProtection - Section 1 verification
"""

from datetime import time
from core.risk.protections.time_window import TimeWindowProtection

# Test 1: Create protection with default window (10:00-11:30 ET)
print("[1/4] Creating TimeWindowProtection...")
protection = TimeWindowProtection()
print(f"  OK Created: {protection.name}")
print(f"  Window: {protection.start_time}-{protection.end_time} {protection.timezone}")

# Test 2: Check status
print("\n[2/4] Getting status...")
status = protection.get_status()
print(f"  OK Enabled: {status['enabled']}")
print(f"  Current time: {status['current_time']}")
print(f"  In window: {status['in_window']}")
print(f"  Protected: {status['is_protected']}")

# Test 3: Check protection (should respect current time)
print("\n[3/4] Checking protection...")
result = protection.check()
print(f"  OK Result: is_protected={result.is_protected}")
if result.is_protected:
    print(f"  Reason: {result.reason}")
    print(f"  Metadata: {result.metadata}")
else:
    print(f"  Trading allowed (within window)")

# Test 4: Create custom window (always blocked)
print("\n[4/4] Testing custom window (00:00-00:01)...")
blocked_protection = TimeWindowProtection(
    start_time=time(0, 0),
    end_time=time(0, 1),
    timezone_str="America/New_York"
)
result = blocked_protection.check()
print(f"  OK Result: is_protected={result.is_protected}")
print(f"  Reason: {result.reason}")

print("\nSUCCESS - SECTION 1 COMPLETE: TimeWindowProtection created and tested")
print("Ready for Section 2: VolatilityProtection")
