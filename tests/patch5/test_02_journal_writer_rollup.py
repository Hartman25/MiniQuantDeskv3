from __future__ import annotations

import json
from pathlib import Path

from core.journal.writer import JournalWriter


def test_rollup_month_creates_monthly_file(tmp_path: Path):
    jw = JournalWriter(base_dir=tmp_path / "journal")

    jw.write_event({"event": "a"})
    jw.write_event({"event": "b"})

    # Derive month from the produced daily file name
    daily_file = next((tmp_path / "journal" / "daily").glob("*.jsonl"))
    day = daily_file.stem  # YYYY-MM-DD
    month = "-".join(day.split("-")[:2])

    out = jw.rollup_month(month)
    assert out.exists()

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    parsed = [json.loads(l)["event"] for l in lines]
    assert "a" in parsed and "b" in parsed
