# tests/patch3/test_01_risk_caps_quantity.py
from decimal import Decimal
from tests.patch3.conftest import FakeRiskResult


def test_risk_caps_quantity_applies_to_order(patch_runtime):
    signals = [
        {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "5",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",
            "stop_loss": "99.50",
        }
    ]

    risk = FakeRiskResult(approved=True, approved_qty=Decimal("1"))

    container, exec_engine = patch_runtime(signals, risk_result=risk)

    # Ensure market order was submitted with capped qty=1
    mkt_calls = [c for c in exec_engine.calls if c[0] == "submit_market_order"]
    assert len(mkt_calls) == 1
    kwargs = mkt_calls[0][1]
    assert str(kwargs["quantity"]) in ("1", "1.0", "1.00")
