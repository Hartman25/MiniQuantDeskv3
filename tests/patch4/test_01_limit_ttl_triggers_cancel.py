# tests/patch4/test_01_limit_ttl_triggers_cancel.py
from core.state import OrderStatus


def test_limit_ttl_triggers_cancel(patch_runtime):
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

    # Force terminal non-fill status
    container, exec_engine = patch_runtime(signals, force_status=OrderStatus.CANCELLED, stale=True)

    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_limit_order" in call_names
    assert "wait_for_order" in call_names
    assert "cancel_order" in call_names
