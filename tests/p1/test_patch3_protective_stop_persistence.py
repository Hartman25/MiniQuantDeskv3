"""
P1 Patch 3 – Protective Stop Persistence Across Restart

INVARIANT:
    After a crash and restart, the system must reload existing protective
    stop orders from the broker so that:
    1. It does not place duplicate stops for positions that already have one.
    2. It can still cancel stops on exit signals.

WHY THIS MATTERS:
    `protective_stop_ids` in app.py is a plain dict that is lost on crash.
    After restart, the system has no memory of outstanding stop orders at
    the broker.  Without reloading them:
    - A new BUY fill would place a SECOND protective stop (duplicate).
    - An EXIT signal would skip the cancel step (orphaned stop remains).

DESIGN:
    New function `_load_protective_stops_from_broker(broker)` in app.py
    that queries broker for open stop orders and returns a dict of
    {symbol: broker_order_id}.  Called once before the main loop.
"""

import pytest
from decimal import Decimal


class TestLoadProtectiveStopsFromBroker:
    """Unit tests for the helper function."""

    def test_returns_empty_when_no_open_stops(self):
        from core.runtime.app import _load_protective_stops_from_broker

        class _Broker:
            def list_open_orders(self):
                return []

        result = _load_protective_stops_from_broker(_Broker())
        assert result == {}

    def test_finds_stop_sell_orders(self):
        from core.runtime.app import _load_protective_stops_from_broker
        from types import SimpleNamespace

        orders = [
            SimpleNamespace(id="BRK-001", symbol="SPY", side="sell",
                            order_type="stop", status="accepted"),
            SimpleNamespace(id="BRK-002", symbol="AAPL", side="sell",
                            order_type="stop", status="new"),
            # Not a stop order — should be ignored
            SimpleNamespace(id="BRK-003", symbol="MSFT", side="buy",
                            order_type="limit", status="accepted"),
            # Stop but BUY side — not a protective stop
            SimpleNamespace(id="BRK-004", symbol="TSLA", side="buy",
                            order_type="stop", status="accepted"),
        ]

        class _Broker:
            def list_open_orders(self):
                return orders

        result = _load_protective_stops_from_broker(_Broker())
        assert result == {"SPY": "BRK-001", "AAPL": "BRK-002"}

    def test_handles_dict_style_orders(self):
        from core.runtime.app import _load_protective_stops_from_broker

        orders = [
            {"id": "BRK-010", "symbol": "QQQ", "side": "sell",
             "order_type": "stop", "status": "accepted"},
        ]

        class _Broker:
            def list_open_orders(self):
                return orders

        result = _load_protective_stops_from_broker(_Broker())
        assert result == {"QQQ": "BRK-010"}

    def test_survives_broker_exception(self):
        from core.runtime.app import _load_protective_stops_from_broker

        class _BrokenBroker:
            def list_open_orders(self):
                raise ConnectionError("nope")

        result = _load_protective_stops_from_broker(_BrokenBroker())
        assert result == {}

    def test_survives_missing_method(self):
        from core.runtime.app import _load_protective_stops_from_broker

        class _MinimalBroker:
            pass

        result = _load_protective_stops_from_broker(_MinimalBroker())
        assert result == {}
