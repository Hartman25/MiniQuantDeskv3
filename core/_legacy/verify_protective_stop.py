"""Quick verification that protective stop is submitted."""
import sys
from pathlib import Path
from decimal import Decimal

# Run the test fixture
sys.path.insert(0, str(Path(__file__).parent))

from tests.patch2.conftest import patch_runtime
from unittest.mock import MagicMock
import tempfile

class FakeMonkeypatch:
    def setattr(self, *args, **kwargs): pass
    def setenv(self, *args, **kwargs): pass

tmpdir = Path(tempfile.mkdtemp())
runtime_fixture = patch_runtime(FakeMonkeypatch(), tmpdir)

signals = [{
    'symbol': 'SPY',
    'side': 'BUY',
    'quantity': '1',
    'order_type': 'MARKET',
    'strategy': 'VWAPMicroMeanReversion',
    'stop_loss': '99.50',
    'stop_loss_price': '99.50',
    'stop_price': '99.50',
}]

container, exec_engine = runtime_fixture(signals)

print("=== EXECUTION ENGINE CALLS ===")
for i, (name, kwargs) in enumerate(exec_engine.calls):
    print(f"\n{i:03d}: {name}")
    for k, v in kwargs.items():
        print(f"      {k}: {v}")

print("\n=== SUMMARY ===")
entry_calls = [c for c in exec_engine.calls if c[0] in ('submit_market_order', 'submit_limit_order')]
stop_calls = [c for c in exec_engine.calls if c[0] == 'submit_stop_order']

print(f"Entry orders submitted: {len(entry_calls)}")
print(f"Stop orders submitted: {len(stop_calls)}")

if stop_calls:
    _, kwargs = stop_calls[0]
    stop_px = kwargs.get('stop_price')
    expected_px = Decimal('99.50')
    actual_px = Decimal(str(stop_px))
    print(f"\nProtective Stop Details:")
    print(f"  Stop price: {stop_px}")
    print(f"  Expected: {expected_px}")
    print(f"  Match: {actual_px == expected_px}")
    print(f"  Symbol: {kwargs.get('symbol')}")
    print(f"  Side: {kwargs.get('side')}")
    print(f"  Quantity: {kwargs.get('quantity')}")
else:
    print("\n❌ NO PROTECTIVE STOP SUBMITTED")
    
print("\n✅ VERIFICATION COMPLETE")
