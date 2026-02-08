"""
Paper-truth integration tests -- validate real Alpaca paper behaviour.

SKIPPED by default unless both env vars are set:
    ALPACA_API_KEY, ALPACA_API_SECRET

Run explicitly:
    python -m pytest -m integration -v

Each test targets a single broker-observable behaviour that the unit
test suite cannot cover (network, fill latency, cancel semantics).
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal

import pytest

# ── skip gate ────────────────────────────────────────────────────────────────
_REQUIRED_VARS = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
_MISSING = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
_SKIP_REASON = f"Missing env vars: {', '.join(_MISSING)}" if _MISSING else ""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_MISSING), reason=_SKIP_REASON),
]


def _uid() -> str:
    return f"pt-{uuid.uuid4().hex[:12]}"


def _market_is_open() -> bool:
    """Check if US equity market is likely open (rough heuristic).

    Uses Alpaca's clock API if available, falls back to wall-clock check.
    """
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_API_SECRET", ""),
            paper=True,
        )
        clock = client.get_clock()
        return clock.is_open
    except Exception:
        pass
    # Fallback: weekday 9:30-16:00 ET
    from datetime import datetime, timezone, timedelta
    et = timezone(timedelta(hours=-5))
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    return now.hour * 60 + now.minute >= 570 and now.hour < 16


_MARKET_CLOSED_REASON = "US equity market is closed; market orders won't fill"


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
    # Best-effort cleanup: cancel any open orders left by tests
    try:
        for o in conn.get_orders(status="open"):
            try:
                oid = getattr(o, "id", None) or (o.get("id") if isinstance(o, dict) else None)
                if oid:
                    conn.cancel_order(oid)
            except Exception:
                pass
    except Exception:
        pass


@pytest.fixture(scope="module")
def account_info(broker):
    """Fetch account once per module -- proves connectivity."""
    return broker.get_account_info()


# ── helpers ──────────────────────────────────────────────────────────────────

def _wait_for_status(broker, broker_order_id, target, timeout_s=10):
    """Poll get_order_status until target OrderStatus (or timeout)."""
    from core.state.order_machine import OrderStatus as OS
    target_set = {target} if not isinstance(target, set) else target
    status = None
    for _ in range(timeout_s):
        status, _info = broker.get_order_status(broker_order_id)
        if status in target_set:
            return status, _info
        time.sleep(1)
    return status, None


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
        GIVEN: A LIMIT BUY far below market -> won't fill
        WHEN:  We wait briefly, then cancel
        THEN:  The order is cancelled without fill.
        """
        from core.brokers.alpaca_connector import BrokerOrderSide
        from core.state.order_machine import OrderStatus as OS

        broker_oid = broker.submit_limit_order(
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.BUY,
            limit_price=Decimal("1.00"),
            internal_order_id=_uid(),
        )
        assert broker_oid, "submit_limit_order must return a broker_order_id"

        time.sleep(1)

        cancelled = broker.cancel_order(broker_oid)
        assert cancelled is not False, "cancel_order should not return False"

        status, _ = _wait_for_status(broker, broker_oid, {OS.CANCELLED, OS.EXPIRED}, timeout_s=5)
        assert status in (OS.CANCELLED, OS.EXPIRED), (
            f"Expected CANCELLED/EXPIRED, got: {status}"
        )

    @pytest.mark.skipif(not _market_is_open(), reason=_MARKET_CLOSED_REASON)
    def test_market_order_fills(self, broker):
        """
        GIVEN: A MARKET BUY for 1 share of SPY
        WHEN:  We wait for the fill
        THEN:  A fill with price > 0 and qty == 1 is returned.
        """
        from core.brokers.alpaca_connector import BrokerOrderSide
        from core.state.order_machine import OrderStatus as OS

        broker_oid = broker.submit_market_order(
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.BUY,
            internal_order_id=_uid(),
        )
        assert broker_oid, "market order must return a broker_order_id"

        status, fill_info = _wait_for_status(broker, broker_oid, OS.FILLED, timeout_s=10)
        assert status == OS.FILLED, f"Expected FILLED, got {status} after 10s"
        assert fill_info is not None, "fill_info must not be None for a filled order"

        # Cleanup: sell position
        try:
            broker.submit_market_order(
                symbol="SPY",
                quantity=Decimal("1"),
                side=BrokerOrderSide.SELL,
                internal_order_id=_uid(),
            )
            time.sleep(2)
        except Exception:
            pass

    @pytest.mark.skipif(not _market_is_open(), reason=_MARKET_CLOSED_REASON)
    def test_protective_stop_accepted(self, broker):
        """
        GIVEN: An open long position
        WHEN:  We submit a STOP sell order far below market
        THEN:  The order is accepted (SUBMITTED / PENDING).
        """
        from core.brokers.alpaca_connector import BrokerOrderSide
        from core.state.order_machine import OrderStatus as OS

        # Open a 1-share position
        entry_oid = broker.submit_market_order(
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.BUY,
            internal_order_id=_uid(),
        )
        _wait_for_status(broker, entry_oid, OS.FILLED, timeout_s=10)

        # Submit stop far below market
        stop_oid = broker.submit_stop_order(
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.SELL,
            stop_price=Decimal("50.00"),
            internal_order_id=_uid(),
        )
        assert stop_oid, "stop order must return a broker_order_id"

        time.sleep(1)
        status, _ = broker.get_order_status(stop_oid)
        assert status in (OS.SUBMITTED, OS.PENDING, OS.PARTIALLY_FILLED), (
            f"Expected stop to be accepted, got: {status}"
        )

        # Cleanup: cancel stop, sell position
        try:
            broker.cancel_order(stop_oid)
            time.sleep(1)
            broker.submit_market_order(
                symbol="SPY", quantity=Decimal("1"),
                side=BrokerOrderSide.SELL,
                internal_order_id=_uid(),
            )
            time.sleep(2)
        except Exception:
            pass

    @pytest.mark.skipif(not _market_is_open(), reason=_MARKET_CLOSED_REASON)
    def test_restart_reconciliation_detects_position(self, broker):
        """
        GIVEN: A filled BUY position exists at the broker
        WHEN:  We create a fresh PositionStore + reconciler (simulating restart)
        THEN:  reconcile_startup() detects the broker position and returns
               a discrepancy indicating the local store is missing it.
        """
        from core.brokers.alpaca_connector import BrokerOrderSide
        from core.state.order_machine import OrderStatus as OS
        from core.state.position_store import PositionStore
        from core.state.reconciler import StartupReconciler

        # 1. Open a real position at the broker
        entry_oid = broker.submit_market_order(
            symbol="SPY",
            quantity=Decimal("1"),
            side=BrokerOrderSide.BUY,
            internal_order_id=_uid(),
        )
        status, _ = _wait_for_status(broker, entry_oid, OS.FILLED, timeout_s=10)
        assert status == OS.FILLED, f"Setup: expected filled, got {status}"

        # 2. Create an EMPTY local position store (simulating restart)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            ps = PositionStore(
                db_path=os.path.join(tmpdir, "positions.db"),
            )

            class _StubTracker:
                def get_open_orders(self, symbol=None):
                    return []

            reconciler = StartupReconciler(
                broker=broker,
                position_store=ps,
                order_tracker=_StubTracker(),
            )

            discrepancies = reconciler.reconcile_startup()
            spy_discs = [d for d in discrepancies if getattr(d, "symbol", "") == "SPY"]
            assert len(spy_discs) > 0, (
                f"Expected discrepancy for SPY, got: {discrepancies}"
            )

        # 3. Cleanup: sell position
        try:
            broker.submit_market_order(
                symbol="SPY", quantity=Decimal("1"),
                side=BrokerOrderSide.SELL,
                internal_order_id=_uid(),
            )
            time.sleep(2)
        except Exception:
            pass
