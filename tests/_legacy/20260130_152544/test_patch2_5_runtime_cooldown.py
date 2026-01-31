from __future__ import annotations

from core.runtime.app import _cooldown_key, _cooldown_should_block


def test_runtime_cooldown_blocks_duplicate_signal():
    cooldown_s = 60
    last_action_ts = {}

    key = _cooldown_key("VWAPMicroMeanReversion", "SPY", "BUY")

    # First action at t=1000 -> should NOT block
    blocked, elapsed = _cooldown_should_block(
        last_action_ts=last_action_ts,
        key=key,
        now_ts=1000.0,
        cooldown_s=cooldown_s,
    )
    assert blocked is False

    # Record the action (what runtime does only on submit)
    last_action_ts[key] = 1000.0

    # Second action at t=1010 -> should block (only 10s elapsed)
    blocked, elapsed = _cooldown_should_block(
        last_action_ts=last_action_ts,
        key=key,
        now_ts=1010.0,
        cooldown_s=cooldown_s,
    )
    assert blocked is True
    assert 9.9 <= elapsed <= 10.1

    # After cooldown passes at t=1061 -> should NOT block
    blocked, elapsed = _cooldown_should_block(
        last_action_ts=last_action_ts,
        key=key,
        now_ts=1061.0,
        cooldown_s=cooldown_s,
    )
    assert blocked is False
    assert elapsed >= 60.0
