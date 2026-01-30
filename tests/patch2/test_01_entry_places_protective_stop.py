# tests/patch2/test_01_entry_places_protective_stop.py
from decimal import Decimal

def test_entry_places_protective_stop(patch_runtime):
    signals = [
        {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",

            # Stop-loss variants (runtime may look for one of these)
            "stop_loss": "99.50",
            "stop_loss_price": "99.50",
            "stop_price": "99.50",
        }

    ]

    lifecycle, exec_engine = patch_runtime(signals)

    # Ensure we placed a protective stop after fill
    call_names = [c[0] for c in exec_engine.calls]
    assert "submit_market_order" in call_names
    assert "wait_for_order" in call_names
    assert "get_fill_details" in call_names
    assert "submit_stop_order" in call_names

    # Ensure stop order was SELL for a long protective stop and uses stop_loss
    stop_calls = [c for c in exec_engine.calls if c[0] == "submit_stop_order"]
    assert len(stop_calls) == 1
    kwargs = stop_calls[0][1]
    assert kwargs["symbol"] == "SPY"
    assert str(kwargs["stop_price"]) == "99.50"
    assert str(kwargs["side"].value).lower() == "sell"
