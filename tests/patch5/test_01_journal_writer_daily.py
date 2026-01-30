from __future__ import annotations

import json
from pathlib import Path

from core.journal.writer import JournalWriter


def test_journal_writer_appends_daily(tmp_path: Path):
    jw = JournalWriter(base_dir=tmp_path / "journal")

    jw.write_event({"event": "unit_test", "x": 1})
    jw.write_event({"event": "unit_test", "x": 2})

    daily_dir = tmp_path / "journal" / "daily"
    files = list(daily_dir.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    e1 = json.loads(lines[0])
    e2 = json.loads(lines[1])
    assert e1["event"] == "unit_test"
    assert e2["x"] == 2

    # auto-enriched fields
    assert "ts" in e1 and "day" in e1 and "month" in e1
