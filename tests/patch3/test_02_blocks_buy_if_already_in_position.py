# tests/patch3/test_02_blocks_buy_if_already_in_position.py
from decimal import Decimal
from tests.patch3.conftest import FakeRiskResult


def test_blocks_buy_if_already_in_position(patch_runtime):
    signals = [
        {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",
            "stop_loss": "99.50",
        }
    ]

    # Risk approves, but we simulate already holding 1 share
    risk = FakeRiskResult(approved=True)
    container, exec_engine = patch_runtime(signals, risk_result=risk, position_qty=Decimal("1"))

    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_market_order" not in call_names, "Should not enter again when already in position"
