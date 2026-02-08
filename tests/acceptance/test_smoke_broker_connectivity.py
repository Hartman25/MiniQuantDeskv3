import os
import pytest

pytestmark = pytest.mark.acceptance


def _get_env(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def _have_alpaca_creds() -> bool:
    # Support both naming schemes:
    # - repo tests mention ALPACA_API_KEY / ALPACA_API_SECRET
    # - Alpaca standard is APCA_API_KEY_ID / APCA_API_SECRET_KEY
    key = _get_env("APCA_API_KEY_ID", "ALPACA_API_KEY")
    sec = _get_env("APCA_API_SECRET_KEY", "ALPACA_API_SECRET")
    base = _get_env("APCA_API_BASE_URL", "ALPACA_BASE_URL") or "https://paper-api.alpaca.markets"
    return bool(key and sec and base)


def _enabled() -> bool:
    # Explicit arming switch so it never runs by accident
    return os.getenv("RUN_BROKER_SMOKE", "").strip() in ("1", "true", "TRUE", "yes", "YES")


@pytest.mark.integration
def test_smoke_alpaca_connectivity_readonly_verbose():
    """
    Read-only broker smoke:
      - connect
      - get_account_info() / balance
      - list_positions()
    Prints each step so you can see exactly where it fails.
    """
    if not _enabled():
        pytest.skip("Set RUN_BROKER_SMOKE=1 to run broker smoke.")
    if not _have_alpaca_creds():
        pytest.skip("Alpaca creds missing in env (APCA_* or ALPACA_*).")

    # Import inside test so collection doesn't fail if deps are missing.
    from core.brokers.alpaca_connector import AlpacaBrokerConnector

    base_url = _get_env("APCA_API_BASE_URL", "ALPACA_BASE_URL") or "https://paper-api.alpaca.markets"
    print(f"[broker_smoke] base_url={base_url}")

    b = AlpacaBrokerConnector(
        api_key=_get_env("APCA_API_KEY_ID", "ALPACA_API_KEY"),
        api_secret=_get_env("APCA_API_SECRET_KEY", "ALPACA_API_SECRET"),
        base_url=base_url,
        paper=True,
    )

    print("[broker_smoke] calling get_account_info() ...")
    acct = b.get_account_info()
    print(f"[broker_smoke] account_info={acct}")

    # You might have different shapes depending on your connector.
    # So we just assert "something non-empty" and print the rest.
    assert acct is not None

    print("[broker_smoke] calling get_positions() ...")
    pos = b.get_positions()
    print(f"[broker_smoke] positions_count={len(pos) if pos is not None else None}")
    print(f"[broker_smoke] positions={pos}")

    assert pos is not None
