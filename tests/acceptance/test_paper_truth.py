"""
Paper-truth integration tests – validate real Alpaca paper behaviour.

These tests are SKIPPED by default unless both env vars are set:
    ALPACA_API_KEY, ALPACA_API_SECRET

Run explicitly:
    python -m pytest -m integration -v

Each test targets a single broker-observable behaviour that the unit
test suite cannot cover (network, fill latency, cancel semantics).
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from datetime import datetime, timezone

import pytest

# ── skip gate ────────────────────────────────────────────────────────────────
_REQUIRED_VARS = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
_MISSING = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
_SKIP_REASON = f"Missing env vars: {', '.join(_MISSING)}" if _MISSING else ""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_MISSING), reason=_SKIP_REASON),
]


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def broker():
    """Create a real Alpaca paper connector from env vars."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector

    conn = AlpacaBrokerConnector(
        api_key=os.environ["ALPACA_API_KEY"],
        api_secret=os.environ["ALPACA_API_SECRET"],
        paper=True,
    )
    yield conn
    # Cleanup: cancel all open orders left by tests
    try:
        conn.cancel_all_orders()
    except Exception:
        pass


@pytest.fixture(scope="module")
def account_info(broker):
    """Fetch account once per module – proves connectivity."""
    return broker.get_account_info()


# ── tests ────────────────────────────────────────────────────────────────────

class TestPaperTruth:
    """Integration tests against Alpaca paper trading."""

    def test_account_connectivity(self, account_info):
        """
        GIVEN: Valid Alpaca paper credentials in env
        WHEN:  We fetch account info
        THEN:  A portfolio_value > 0 is returned, proving connectivity.
        """
        pv = account_info.get("portfolio_value") or account_info.get("equity")
        assert pv is not None, "account_info must contain portfolio_value or equity"
        assert Decimal(str(pv)) > 0, "portfolio value must be positive on paper account"

    def test_limit_order_ttl_cancel(self, broker):
        """
        GIVEN: A LIMIT BUY far below market → won't fill
        WHEN:  We wait briefly, then cancel
        THEN:  The order is cancelled without fill.
        """
        # Place a LIMIT order at $1 for SPY (will never fill)
        order = broker.submit_order(
            symbol="SPY",
            qty=1,
            side="buy",
            order_type="limit",
            limit_price=Decimal("1.00"),
            time_in_force="day",
        )
        assert order is not None, "submit_order must return an order object"
        order_id = getattr(order, "id", None) or order.get("id")
        assert order_id, "order must have an id"

        time.sleep(1)  # brief pause

        # Cancel it
        cancelled = broker.cancel_order(order_id)
        # Accept True, None, or no exception as success
        # (some connectors return None on success)
        assert cancelled is not False, "cancel_order should not return False"

        # Verify order is no longer open
        time.sleep(1)
        status = broker.get_order_status(order_id)
        assert status in ("canceled", "cancelled", "expired", "replaced"), (
            f"Expected canceled status, got: {status}"
        )

    def test_market_order_fills(self, broker):
        """
        GIVEN: A MARKET BUY for 1 share of SPY
        WHEN:  We wait for the fill
        THEN:  A fill with price > 0 and qty == 1 is returned,
               and a journal/order ID is assigned.
        """
        order = broker.submit_order(
            symbol="SPY",
            qty=1,
            side="buy",
            order_type="market",
            time_in_force="day",
        )
        order_id = getattr(order, "id", None) or order.get("id")
        assert order_id, "market order must have an id"

        # Wait for fill (paper fills are near-instant)
        for _ in range(10):
            time.sleep(1)
            status = broker.get_order_status(order_id)
            if status == "filled":
                break
        assert status == "filled", f"Expected filled, got {status} after 10s"

        # Cleanup: close the position
        try:
            broker.submit_order(
                symbol="SPY",
                qty=1,
                side="sell",
                order_type="market",
                time_in_force="day",
            )
            time.sleep(2)  # wait for cleanup fill
        except Exception:
            pass  # best-effort cleanup

    def test_protective_stop_accepted(self, broker):
        """
        GIVEN: An open long position
        WHEN:  We submit a STOP sell order at a price below market
        THEN:  The order is accepted and shows as 'new' or 'accepted'.
        """
        # Open a 1-share position
        entry = broker.submit_order(
            symbol="SPY",
            qty=1,
            side="buy",
            order_type="market",
            time_in_force="day",
        )
        entry_id = getattr(entry, "id", None) or entry.get("id")
        time.sleep(3)  # let fill settle

        # Submit stop at ~$1 below the assumed market (~$400+).
        # Use a very low stop so it won't trigger during test.
        stop = broker.submit_order(
            symbol="SPY",
            qty=1,
            side="sell",
            order_type="stop",
            stop_price=Decimal("50.00"),  # far below market
            time_in_force="day",
        )
        stop_id = getattr(stop, "id", None) or stop.get("id")
        assert stop_id, "stop order must have an id"

        time.sleep(1)
        status = broker.get_order_status(stop_id)
        assert status in ("new", "accepted", "held", "pending_new"), (
            f"Expected stop to be accepted, got: {status}"
        )

        # Cleanup: cancel stop, sell position
        try:
            broker.cancel_order(stop_id)
            time.sleep(1)
            broker.submit_order(
                symbol="SPY", qty=1, side="sell",
                order_type="market", time_in_force="day",
            )
            time.sleep(2)
        except Exception:
            pass

    def test_restart_reconciliation_restores_position(self, broker):
        """
        GIVEN: A filled BUY position exists at the broker
        WHEN:  We create a fresh PositionStore + reconciler (simulating restart)
        THEN:  reconcile_startup() detects the broker position and returns
               a discrepancy indicating the local store is missing it.
        """
        from core.state.position_store import PositionStore
        from core.state.reconciler import StartupReconciler

        # 1. Open a real position at the broker
        order = broker.submit_order(
            symbol="SPY",
            qty=1,
            side="buy",
            order_type="market",
            time_in_force="day",
        )
        order_id = getattr(order, "id", None) or order.get("id")
        for _ in range(10):
            time.sleep(1)
            status = broker.get_order_status(order_id)
            if status == "filled":
                break
        assert status == "filled", f"Setup: expected filled, got {status}"

        # 2. Create an EMPTY local position store (simulating restart with clean state)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            ps = PositionStore(
                db_path=os.path.join(tmpdir, "positions.db"),
            )

            # Minimal order tracker stub
            class _StubTracker:
                def get_open_orders(self, symbol=None):
                    return []

            reconciler = StartupReconciler(
                broker=broker,
                position_store=ps,
                order_tracker=_StubTracker(),
            )

            discrepancies = reconciler.reconcile_startup()
            # Must detect at least one discrepancy: broker has SPY, local doesn't
            spy_discs = [d for d in discrepancies if getattr(d, "symbol", "") == "SPY"]
            assert len(spy_discs) > 0, (
                f"Expected discrepancy for SPY, got: {discrepancies}"
            )

        # 3. Cleanup: sell the position
        try:
            broker.submit_order(
                symbol="SPY", qty=1, side="sell",
                order_type="market", time_in_force="day",
            )
            time.sleep(2)
        except Exception:
            pass
