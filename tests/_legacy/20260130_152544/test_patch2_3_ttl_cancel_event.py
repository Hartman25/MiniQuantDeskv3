from __future__ import annotations

from unittest.mock import Mock

from core.runtime.app import _emit_limit_ttl_cancel_event


def test_ttl_cancel_event_schema():
    """
    PATCH 2.3: TTL cancel event must use canonical schema and stable keys.
    Unit-test the emitter directly (avoid brittle runtime loop patching).
    """
    mock_journal = Mock()

    _emit_limit_ttl_cancel_event(
        journal=mock_journal,
        run_id="run_test_001",
        internal_order_id="internal_abc",
        broker_order_id="broker_123",
        symbol="SPY",
        side="BUY",
        qty="1",
        order_type="LIMIT",
        limit_price="99.90",
        strategy="VWAPMicroMeanReversion",
        ttl_seconds=5,
        final_status="NEW",
        reason="limit_ttl_expired_no_chase",
    )

    mock_journal.write_event.assert_called_once()
    e = mock_journal.write_event.call_args[0][0]

    required_keys = {
        "event", "ts_utc", "run_id",
        "internal_order_id", "broker_order_id",
        "symbol", "side", "qty",
        "order_type", "limit_price",
        "strategy", "ttl_seconds",
        "final_status", "reason",
    }
    assert required_keys.issubset(set(e.keys()))
    assert e["event"] == "ORDER_TTL_CANCEL"
    assert e["reason"] == "limit_ttl_expired_no_chase"
    assert e["order_type"] == "LIMIT"
    assert e["symbol"] == "SPY"
