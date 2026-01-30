# tests/patch2/test_03_limit_ttl_cancels_unfilled.py
from core.state import OrderStatus

def test_limit_ttl_cancels_unfilled_entry(patch_runtime):
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
            "stop_loss_price": "99.50",
            "stop_price": "99.50",
        }

    ]

    lifecycle, exec_engine = patch_runtime(signals, force_status=OrderStatus.CANCELLED)

    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_limit_order" in call_names
    assert "wait_for_order" in call_names
    assert "cancel_order" in call_names

    # Ensure we did NOT place a protective stop (since entry didn't fill)
    assert "submit_stop_order" not in call_names
