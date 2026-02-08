import os
import pytest
from decimal import Decimal

from core.brokers.alpaca_connector import AlpacaBrokerConnector, BrokerConnectionError


pytestmark = pytest.mark.acceptance


def _env(name: str) -> str | None:
    return os.getenv(name)


@pytest.mark.skipif(
    not _env("MQD_ALLOW_LIVE_SMOKE"),
    reason="Live broker smoke tests disabled (set MQD_ALLOW_LIVE_SMOKE=1)"
)
@pytest.mark.skipif(
    not _env("ALPACA_API_KEY") or not _env("ALPACA_API_SECRET"),
    reason="Alpaca credentials not set"
)
def test_alpaca_readonly_smoke_verbose(caplog):
    """
    READ-ONLY Alpaca smoke test.

    Verifies:
      1. Connector initializes
      2. Account info can be fetched
      3. Positions can be fetched

    NO ORDERS are placed.
    """

    caplog.set_level("INFO")

    print("\n[SMOKE] Initializing AlpacaBrokerConnector (paper mode)")
    broker = AlpacaBrokerConnector(
        api_key=os.environ["ALPACA_API_KEY"],
        api_secret=os.environ["ALPACA_API_SECRET"],
        paper=True,
    )

    print("[SMOKE] Fetching account info")
    acct = broker.get_account_info()

    print("[SMOKE] Account info received:")
    for k, v in acct.items():
        print(f"  {k}: {v}")

    assert isinstance(acct["portfolio_value"], Decimal)
    assert acct["portfolio_value"] > 0

    print("[SMOKE] Fetching open positions")
    positions = broker.get_positions()

    print(f"[SMOKE] Positions returned: {len(positions)}")
    for p in positions:
        print(
            f"  {p.symbol} | qty={p.quantity} | entry={p.entry_price} | "
            f"unrealized={p.unrealized_pnl}"
        )

    # Sanity only — zero positions is fine
    assert isinstance(positions, list)

    print("[SMOKE] Alpaca READ-ONLY smoke test PASSED")
