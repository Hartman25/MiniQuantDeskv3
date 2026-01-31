import json
from pathlib import Path

from core.journal.writer import JournalWriter
from core.journal.ids import new_trade_id, new_event_id, get_run_id


def test_patch5b_writer_records_ids(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    w = JournalWriter(enable_monthly_rollup=True)

    run_id = get_run_id()
    trade_id = new_trade_id("SPY", "VWAPMicroMeanReversion")
    evt_id = new_event_id()

    w.write_event(
        "signal_received",
        {
            "run_id": run_id,
            "trade_id": trade_id,
            "event_id": evt_id,
            "symbol": "SPY",
            "strategy": "VWAPMicroMeanReversion",
            "side": "BUY",
        },
    )

    daily = next((tmp_path / "journal" / "daily").glob("*.jsonl"))
    lines = daily.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])

    assert obj["event"] == "signal_received"
    assert obj["run_id"] == run_id
    assert obj["trade_id"] == trade_id
    assert obj["event_id"] == evt_id
    assert "ts_utc" in obj

    monthly = next((tmp_path / "journal" / "monthly").glob("*.jsonl"))
    mlines = monthly.read_text(encoding="utf-8").strip().splitlines()
    assert len(mlines) == 1
