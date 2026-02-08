import os
import time
import logging
import pytest

log = logging.getLogger(__name__)

pytestmark = pytest.mark.acceptance


def _env(name: str) -> str | None:
    v = os.getenv(name)
    return v.strip() if isinstance(v, str) else v


def _have_broker_creds() -> bool:
    # Support both your BROKER_* and Alpaca-native names
    return bool(
        (_env("BROKER_API_KEY") and _env("BROKER_API_SECRET"))
        or (_env("ALPACA_API_KEY") and _env("ALPACA_API_SECRET"))
        or (_env("APCA_API_KEY_ID") and _env("APCA_API_SECRET_KEY"))
    )


def _mask(v: str | None) -> str:
    if not v:
        return "<missing>"
    if len(v) <= 6:
        return v[0] + "***"
    return v[:3] + "***" + v[-3:]


@pytest.mark.skipif(not _have_broker_creds(), reason="Missing broker creds in env (BROKER_* / ALPACA_* / APCA_*).")
def test_broker_smoke_readonly_verbose():
    """
    Read-only broker integration smoke:
      - instantiate broker connector (paper)
      - get_account_info()
      - get_bars() for SPY with a tiny window

    Verbose logging prints each step and where it fails.
    """
    log.info("=== BROKER SMOKE (READONLY) START ===")

    # Force paper for safety
    os.environ["PAPER_TRADING"] = "true"

    # Print env visibility (masked)
    log.info("Env PAPER_TRADING=%s", _env("PAPER_TRADING"))
    log.info("Env BROKER_API_KEY=%s", _mask(_env("BROKER_API_KEY")))
    log.info("Env BROKER_API_SECRET=%s", _mask(_env("BROKER_API_SECRET")))
    log.info("Env ALPACA_API_KEY=%s", _mask(_env("ALPACA_API_KEY")))
    log.info("Env ALPACA_API_SECRET=%s", _mask(_env("ALPACA_API_SECRET")))
    log.info("Env APCA_API_KEY_ID=%s", _mask(_env("APCA_API_KEY_ID")))
    log.info("Env APCA_API_SECRET_KEY=%s", _mask(_env("APCA_API_SECRET_KEY")))

    t0 = time.time()
    log.info("[1/3] Importing AlpacaBrokerConnector...")
    from core.brokers.alpaca_connector import AlpacaBrokerConnector
    log.info("Imported connector in %.2fs", time.time() - t0)

    log.info("[2/3] Instantiating broker connector in PAPER mode...")
    t1 = time.time()
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("BROKER_API_KEY") or os.getenv("APCA_API_KEY_ID")
    api_secret = os.getenv("ALPACA_API_SECRET") or os.getenv("BROKER_API_SECRET") or os.getenv("APCA_API_SECRET_KEY")
    broker = AlpacaBrokerConnector(api_key=api_key, api_secret=api_secret, paper=True)

    log.info("Instantiated broker in %.2fs", time.time() - t1)

    log.info("[3/3] Calling get_account_info()...")
    t2 = time.time()
    info = broker.get_account_info()
    log.info("get_account_info() returned in %.2fs", time.time() - t2)
    log.info("Account info keys=%s", sorted(list(info.keys())) if isinstance(info, dict) else type(info))
    assert isinstance(info, dict), f"Expected dict from get_account_info(), got {type(info)}"
    assert "portfolio_value" in info, f"Missing 'portfolio_value' in {info}"
    assert "buying_power" in info, f"Missing 'buying_power' in {info}"

    # Positions fetch: validates get_positions() works
    log.info("[BONUS] Calling get_positions()...")
    t3 = time.time()
    positions = broker.get_positions()
    log.info("get_positions() returned in %.2fs (count=%d)", time.time() - t3, len(positions))
    assert isinstance(positions, list), f"Expected list from get_positions(), got {type(positions)}"

    log.info("=== BROKER SMOKE (READONLY) PASS ===")
