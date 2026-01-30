import json
from pathlib import Path

from core.journal.writer import JournalWriter
from core.journal.ids import new_trade_id, new_event_id, new_order_id, get_run_id


def test_patch5b_trade_chain_same_trade_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    w = JournalWriter(enable_monthly_rollup=False)

    run_id = get_run_id()
    trade_id = new_trade_id("SPY", "VWAPMicroMeanReversion")
    order_id = new_order_id()

    w.write_event("signal_received", {"run_id": run_id, "trade_id": trade_id, "event_id": new_event_id(), "symbol": "SPY"})
    w.write_event("risk_decision", {"run_id": run_id, "trade_id": trade_id, "event_id": new_event_id(), "approved": True, "approved_qty": "1"})
    w.write_event("order_submitted", {"run_id": run_id, "trade_id": trade_id, "event_id": new_event_id(), "internal_order_id": order_id, "order_type": "MARKET", "side": "BUY", "qty": "1"})
    w.write_event("order_terminal", {"run_id": run_id, "trade_id": trade_id, "event_id": new_event_id(), "internal_order_id": order_id, "status": "FILLED"})
    w.write_event("protective_stop_submitted", {"run_id": run_id, "trade_id": trade_id, "event_id": new_event_id(), "stop_price": "99.50"})

    daily = next((tmp_path / "journal" / "daily").glob("*.jsonl"))
    objs = [json.loads(line) for line in daily.read_text(encoding="utf-8").strip().splitlines()]

    assert len(objs) == 5
    assert all(o["trade_id"] == trade_id for o in objs)
    assert objs[2]["internal_order_id"] == order_id
    assert objs[3]["internal_order_id"] == order_id
