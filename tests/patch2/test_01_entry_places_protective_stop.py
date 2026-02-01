from decimal import Decimal
import pytest


def _dump_calls(exec_engine) -> str:
    calls = getattr(exec_engine, "calls", [])
    return "\n".join([f"{i:03d}: {name} {kwargs}" for i, (name, kwargs) in enumerate(calls)])


def _first_call(exec_engine, *names: str):
    want = set(names)
    for name, kwargs in getattr(exec_engine, "calls", []):
        if name in want:
            return name, kwargs
    return None


def _all_calls(exec_engine, *names: str):
    want = set(names)
    return [(n, kw) for (n, kw) in getattr(exec_engine, "calls", []) if n in want]


@pytest.mark.integration
def test_entry_places_protective_stop(patch_runtime):
    """
    P0 / PATCH2.1 (non-negotiable):
      Given an ENTRY signal with a stop-loss price:
        1) submit a MARKET BUY entry
        2) after entry is filled, submit a protective STOP SELL with that stop price
    """
    stop_px = Decimal("99.50")

    signals = [{
        "symbol": "SPY",
        "side": "BUY",
        "quantity": "1",
        "order_type": "MARKET",
        "strategy": "VWAPMicroMeanReversion",
        # runtime may read any of these, so provide all
        "stop_loss": str(stop_px),
        "stop_loss_price": str(stop_px),
        "stop_price": str(stop_px),
    }]

    _container, exec_engine = patch_runtime(signals)

    # 1) Entry submitted
    entry = _first_call(exec_engine, "submit_market_order", "submit_limit_order")
    assert entry is not None, (
        "No entry order submission recorded.\n\n"
        f"CALLS:\n{_dump_calls(exec_engine)}"
    )
    entry_name, entry_kwargs = entry
    assert str(entry_kwargs.get("symbol", "")).upper() == "SPY", (
        "Entry order not for SPY.\n\n"
        f"ENTRY:\n{entry_name} {entry_kwargs}\n\n"
        f"CALLS:\n{_dump_calls(exec_engine)}"
    )
    assert "BUY" in str(entry_kwargs.get("side", "")).upper(), (
        "Entry order is not BUY.\n\n"
        f"ENTRY:\n{entry_name} {entry_kwargs}\n\n"
        f"CALLS:\n{_dump_calls(exec_engine)}"
    )

    # 2) Protective stop submitted
    stop_calls = _all_calls(exec_engine, "submit_stop_order")
    assert stop_calls, (
        "No protective STOP submission recorded.\n\n"
        f"CALLS:\n{_dump_calls(exec_engine)}"
    )

    # Confirm stop price made it into stop order kwargs (key name may vary)
    def extract_px(kw):
        for k in ("stop_price", "stop_loss_price", "stop_loss", "price"):
            if k in kw and kw[k] is not None:
                try:
                    return Decimal(str(kw[k]))
                except Exception:
                    pass
        return None

    assert any(extract_px(kw) == stop_px for _, kw in stop_calls), (
        f"STOP order submitted, but stop price {stop_px} not found.\n\n"
        f"STOP CALLS:\n{stop_calls}\n\n"
        f"CALLS:\n{_dump_calls(exec_engine)}"
    )
