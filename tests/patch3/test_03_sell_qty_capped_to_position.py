# tests/patch3/test_03_sell_qty_capped_to_position.py
from decimal import Decimal
from tests.patch3.conftest import FakeRiskResult


def test_sell_qty_capped_to_position(patch_runtime):
    signals = [
        # Exit signal only; pretend we already hold 1 share
        {
            "symbol": "SPY",
            "side": "SELL",
            "quantity": "5",  # intentionally too large
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",
        }
    ]

    risk = FakeRiskResult(approved=True)

    container, exec_engine = patch_runtime(signals, risk_result=risk, position_qty=Decimal("1"))

    mkt_calls = [c for c in exec_engine.calls if c[0] == "submit_market_order"]
    assert len(mkt_calls) == 1
    kwargs = mkt_calls[0][1]

    # Quantity should be capped to position size = 1
    assert str(kwargs["quantity"]) in ("1", "1.0", "1.00")

    # Side should be SELL (case-insensitive)
    assert str(kwargs["side"].value).lower() == "sell"
