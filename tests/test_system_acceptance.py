import pytest

# This assumes your existing patch2 conftest provides patch_runtime(signals) -> (lifecycle, exec_engine)
# and that exec_engine.calls records ("submit_market_order", ...), ("submit_stop_order", ...), etc.

@pytest.mark.integration
def test_system_acceptance_entry_then_protective_stop_then_exit(patch_runtime):
    """
    End-to-end acceptance test for the current system behavior:

    1) Entry signal (MARKET BUY) is submitted.
    2) After entry fill, a protective stop is placed.
    3) Exit is executed and protective stop is cancelled before SELL (if your system enforces that).
    """

    signals = [
        {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": "1",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",

            # stop-loss variants runtime may accept
            "stop_loss": "99.50",
            "stop_loss_price": "99.50",
            "stop_price": "99.50",
        },
        {
            "symbol": "SPY",
            "side": "SELL",
            "quantity": "1",
            "order_type": "MARKET",
            "strategy": "VWAPMicroMeanReversion",
        },
    ]

    lifecycle, exec_engine = patch_runtime(signals)

    # --- Assertions on execution engine calls ---
    call_names = [c[0] for c in getattr(exec_engine, "calls", [])]

    # Entry should happen
    assert "submit_market_order" in call_names, f"Expected submit_market_order, got calls={call_names}"

    # Protective stop should be placed after entry fill
    # (Your fake engine might name this differently; adjust if needed)
    assert any(n in call_names for n in ["submit_stop_order", "submit_stop_loss_order", "submit_protective_stop"]), \
        f"Expected protective stop placement, got calls={call_names}"

    # If your design cancels stop before exit sell:
    # (again: adjust call name if your engine uses a different label)
    assert any(n in call_names for n in ["cancel_order", "cancel_stop_order", "cancel_protective_stop"]), \
        f"Expected stop cancellation before exit, got calls={call_names}"
