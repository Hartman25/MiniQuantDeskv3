# tests/patch2/test_02_exit_cancels_stop_before_sell.py

def test_exit_cancels_protective_stop_before_sell(patch_runtime):
    signals = [
        # First: enter long (will place protective stop)
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
        },

        # Then: exit
        {
            "symbol": "SPY",
            "side": "SELL",
            "quantity": "1",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",
        },
    ]

    lifecycle, exec_engine = patch_runtime(signals)

    calls = exec_engine.calls

    # We expect: submit_stop_order (after buy fill) -> cancel_order (on sell) -> submit_market_order (sell)
    idx_stop = next(i for i, c in enumerate(calls) if c[0] == "submit_stop_order")
    idx_cancel = next(i for i, c in enumerate(calls) if c[0] == "cancel_order")

    idx_sell = None
    for i in range(idx_cancel + 1, len(calls)):
        if calls[i][0] == "submit_market_order":
            if str(calls[i][1]["side"].value).lower() == "sell":
                idx_sell = i
                break

    assert idx_cancel > idx_stop, "Cancel must happen after protective stop exists"
    assert idx_sell is not None, "Expected a SELL market order submission"
    assert idx_cancel < idx_sell, "Protective stop must be cancelled BEFORE SELL is submitted"
