# tests/patch4/test_03_no_cancel_if_filled.py
from core.state import OrderStatus


def test_no_cancel_if_filled(patch_runtime):
    signals = [
        {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "order_type": "LIMIT",
            "limit_price": "99.90",
            "ttl_seconds": 5,
            "strategy": "VWAPMicroMeanReversion",
            "stop_loss": "99.50",
        }
    ]

    container, exec_engine = patch_runtime(signals, force_status=OrderStatus.FILLED, stale=True)

    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_limit_order" in call_names
    assert "cancel_order" not in call_names, "Should not cancel if order filled"
    assert "submit_stop_order" in call_names, "Filled entry should place protective stop (Patch 2)"
