from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from core.runtime.app import _emit_auto_heal_event


class DummyDisc:
    def __init__(self):
        self.type = "missing_position"
        self.symbol = "SPY"
        self.local_value = None
        self.broker_value = "10 @ 450"
        self.resolution = "paper_auto_heal"
        self.timestamp = datetime.now(timezone.utc)


def test_auto_heal_event_schema():
    j = Mock()
    d = DummyDisc()

    _emit_auto_heal_event(
        journal=j,
        run_id="run_test_001",
        discrepancy=d,
        action="open_position",
        strategy="VWAPMicroMeanReversion",
    )

    j.write_event.assert_called_once()
    e = j.write_event.call_args[0][0]
    assert e["event"] == "AUTO_HEAL_APPLIED"
    assert e["resolution"] == "paper_auto_heal"
    assert e["discrepancy_type"] == "missing_position"
    assert e["symbol"] == "SPY"
    assert e["action"] == "open_position"
