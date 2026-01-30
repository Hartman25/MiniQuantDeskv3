"""
Test app.py protection integration - Section 4 verification
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test 1: Check imports (should not import ProtectionContext)
print("[1/3] Checking imports in app.py...")
with open(r"C:\Users\Zacha\Desktop\MiniQuantDeskv2\core\runtime\app.py", "r") as f:
    content = f.read()
    
if "ProtectionContext" in content:
    print("  ERROR: Still imports ProtectionContext!")
    print("  Found in content")
else:
    print("  OK: ProtectionContext import removed")

if "protections_old" in content:
    print("  ERROR: Still imports from protections_old!")
else:
    print("  OK: No protections_old imports")

# Test 2: Check API usage
print("\n[2/3] Checking protection API usage...")
if "prot_ctx = ProtectionContext" in content:
    print("  ERROR: Still creates ProtectionContext objects!")
else:
    print("  OK: No ProtectionContext creation")

if "protections.check(prot_ctx)" in content:
    print("  ERROR: Still uses old API (passing ProtectionContext)")
else:
    print("  OK: Old API calls removed")

if "protections.check(" in content and "symbol=" in content:
    print("  OK: Uses new API (symbol parameter)")
else:
    print("  WARNING: Protection check might not be using new API")

if "prot_result.is_protected" in content:
    print("  OK: Uses new ProtectionResult.is_protected")
else:
    print("  WARNING: Might not be checking is_protected field")

# Test 3: Check container integration
print("\n[3/3] Checking container integration...")
if "container.get_protection_stack()" in content:
    print("  ERROR: Still uses get_protection_stack()!")
else:
    print("  OK: get_protection_stack() removed")

if "container.get_protections()" in content:
    print("  OK: Uses container.get_protections()")
else:
    print("  ERROR: Not using container.get_protections()!")

print("\n" + "="*60)
if (
    "ProtectionContext" not in content 
    and "protections_old" not in content
    and "get_protection_stack()" not in content
    and "get_protections()" in content
    and "prot_result.is_protected" in content
):
    print("SUCCESS - SECTION 4 COMPLETE: app.py migrated to ProtectionManager")
    print("Ready for Section 5: Clean up legacy imports")
else:
    print("ISSUES FOUND - Review output above")
