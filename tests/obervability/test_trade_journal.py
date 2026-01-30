# tests/observability/test_trade_journal.py
from __future__ import annotations

import json
from pathlib import Path

from core.journal.trade_journal import TradeJournal, TradeIds, build_trade_event, SCHEMA_VERSION


def test_trade_journal_writes_daily_jsonl(tmp_path: Path) -> None:
    tj = TradeJournal(base_dir=tmp_path)

    ids = TradeIds(run_id=TradeJournal.new_run_id(), trade_id=TradeJournal.new_trade_id())
    evt = build_trade_event(
        event_type="ORDER_SUBMIT",
        ids=ids,
        internal_order_id="ORD_TEST_1",
        broker_order_id="BROKER_123",
        symbol="SPY",
        side="buy",
        qty="1",
        order_type="LIMIT",
        limit_price="99.90",
        strategy="VWAPMicroMeanReversion",
        reason={"why": "unit_test"},
    )

    tj.emit(evt)
    tj.close()

    trades_dir = tmp_path / "trades"
    assert trades_dir.exists()

    files = list(trades_dir.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    obj = json.loads(lines[0])
    assert obj["schema_version"] == SCHEMA_VERSION
    assert obj["event_type"] == "ORDER_SUBMIT"
    assert obj["run_id"] == ids.run_id
    assert obj["trade_id"] == ids.trade_id
    assert obj["internal_order_id"] == "ORD_TEST_1"
    assert obj["broker_order_id"] == "BROKER_123"
