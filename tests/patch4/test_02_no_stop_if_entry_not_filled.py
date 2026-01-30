# tests/patch4/test_02_no_stop_if_entry_not_filled.py
from core.state import OrderStatus


def test_no_stop_if_entry_not_filled(patch_runtime):
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

    container, exec_engine = patch_runtime(signals, force_status=OrderStatus.CANCELLED, stale=True)

    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_stop_order" not in call_names, "No protective stop should be placed if entry never fills"
