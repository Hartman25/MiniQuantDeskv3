"""
Phase 2 — NO-TRADE Filter Tests

Invariants covered:
  P2-INV-05: NO-TRADE reasons are first-class (structured, not bool)
  P2-INV-06: Session window enforcement
  P2-INV-07: Blackout near open/close
  P2-INV-08: Post-stop cooldown
"""
from __future__ import annotations

from datetime import time

import pytest

from strategies.no_trade_filter import (
    NoTradeFilterConfig,
    NoTradeReasonCode,
    check_no_trade,
)


# ---------------------------------------------------------------------------
# Defaults: a "good" context where trading should be allowed
# ---------------------------------------------------------------------------

def _allowed_kwargs(**overrides):
    """Build kwargs for check_no_trade with all conditions passing."""
    defaults = dict(
        bar_time_et=time(10, 30),  # middle of session
        trades_today=0,
        daily_pnl_est=0.0,
        bars_since_last_stop=None,
        warmup_complete=True,
        config=NoTradeFilterConfig(),
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# P2-INV-05: Structured reasons (not bool)
# ---------------------------------------------------------------------------

class TestStructuredReasons:
    def test_no_reasons_when_all_clear(self):
        reasons = check_no_trade(**_allowed_kwargs())
        assert reasons == [], f"Expected empty list, got {reasons}"

    def test_reasons_are_list_of_objects(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(8, 0)))
        assert isinstance(reasons, list)
        assert len(reasons) >= 1
        r = reasons[0]
        assert hasattr(r, "code")
        assert hasattr(r, "message")

    def test_multiple_conditions_produce_multiple_reasons(self):
        """When multiple blocking conditions are true, all reasons reported."""
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(8, 0),    # outside session
            trades_today=5,            # max trades exceeded
            warmup_complete=False,     # warmup not done
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION in codes
        assert NoTradeReasonCode.MAX_TRADES_REACHED in codes
        assert NoTradeReasonCode.WARMUP_INCOMPLETE in codes
        assert len(reasons) >= 3


# ---------------------------------------------------------------------------
# P2-INV-06: Session window enforcement
# ---------------------------------------------------------------------------

class TestSessionWindow:
    def test_before_session_blocked(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(9, 0)))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION in codes

    def test_after_session_blocked(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(12, 0)))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION in codes

    def test_within_session_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(10, 30)))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION not in codes

    def test_at_session_start_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(10, 0)))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION not in codes

    def test_at_session_end_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(bar_time_et=time(11, 30)))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.OUTSIDE_SESSION not in codes


# ---------------------------------------------------------------------------
# P2-INV-07: Blackout near open/close
# ---------------------------------------------------------------------------

class TestBlackout:
    def test_blackout_near_open(self):
        """Within blackout_open_minutes after market open => blocked."""
        cfg = NoTradeFilterConfig(
            blackout_open_minutes=5,
            market_open_time=time(9, 30),
            # widen session to include 9:30 for this test
            trade_start_time=time(9, 30),
        )
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(9, 32),
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.BLACKOUT_NEAR_OPEN in codes

    def test_after_blackout_open_allowed(self):
        cfg = NoTradeFilterConfig(
            blackout_open_minutes=5,
            market_open_time=time(9, 30),
            trade_start_time=time(9, 30),
        )
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(9, 36),
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.BLACKOUT_NEAR_OPEN not in codes

    def test_blackout_near_close(self):
        """Within blackout_close_minutes before market close => blocked."""
        cfg = NoTradeFilterConfig(
            blackout_close_minutes=5,
            market_close_time=time(16, 0),
            # widen session to include 15:55
            trade_end_time=time(16, 0),
        )
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(15, 56),
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.BLACKOUT_NEAR_CLOSE in codes

    def test_before_blackout_close_allowed(self):
        cfg = NoTradeFilterConfig(
            blackout_close_minutes=5,
            market_close_time=time(16, 0),
            trade_end_time=time(16, 0),
        )
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(15, 50),
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.BLACKOUT_NEAR_CLOSE not in codes

    def test_zero_blackout_disabled(self):
        """blackout_*_minutes=0 disables the check."""
        cfg = NoTradeFilterConfig(
            blackout_open_minutes=0,
            blackout_close_minutes=0,
            trade_start_time=time(9, 30),
            trade_end_time=time(16, 0),
        )
        reasons = check_no_trade(**_allowed_kwargs(
            bar_time_et=time(9, 30),
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.BLACKOUT_NEAR_OPEN not in codes
        assert NoTradeReasonCode.BLACKOUT_NEAR_CLOSE not in codes


# ---------------------------------------------------------------------------
# P2-INV-08: Post-stop cooldown
# ---------------------------------------------------------------------------

class TestCooldown:
    def test_cooldown_blocks_after_stop(self):
        cfg = NoTradeFilterConfig(cooldown_after_stop_bars=5)
        reasons = check_no_trade(**_allowed_kwargs(
            bars_since_last_stop=2,
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.COOLDOWN_AFTER_STOP in codes

    def test_cooldown_allows_after_enough_bars(self):
        cfg = NoTradeFilterConfig(cooldown_after_stop_bars=5)
        reasons = check_no_trade(**_allowed_kwargs(
            bars_since_last_stop=5,
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.COOLDOWN_AFTER_STOP not in codes

    def test_no_cooldown_when_no_stop(self):
        cfg = NoTradeFilterConfig(cooldown_after_stop_bars=5)
        reasons = check_no_trade(**_allowed_kwargs(
            bars_since_last_stop=None,
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.COOLDOWN_AFTER_STOP not in codes

    def test_zero_cooldown_disabled(self):
        cfg = NoTradeFilterConfig(cooldown_after_stop_bars=0)
        reasons = check_no_trade(**_allowed_kwargs(
            bars_since_last_stop=0,
            config=cfg,
        ))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.COOLDOWN_AFTER_STOP not in codes


# ---------------------------------------------------------------------------
# Max trades / daily loss
# ---------------------------------------------------------------------------

class TestMaxTradesAndDailyLoss:
    def test_max_trades_blocks(self):
        reasons = check_no_trade(**_allowed_kwargs(trades_today=1))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.MAX_TRADES_REACHED in codes

    def test_below_max_trades_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(trades_today=0))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.MAX_TRADES_REACHED not in codes

    def test_daily_loss_limit_blocks(self):
        reasons = check_no_trade(**_allowed_kwargs(daily_pnl_est=-3.00))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.DAILY_LOSS_LIMIT in codes

    def test_above_daily_loss_limit_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(daily_pnl_est=-1.00))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.DAILY_LOSS_LIMIT not in codes


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_warmup_incomplete_blocks(self):
        reasons = check_no_trade(**_allowed_kwargs(warmup_complete=False))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.WARMUP_INCOMPLETE in codes

    def test_warmup_complete_allowed(self):
        reasons = check_no_trade(**_allowed_kwargs(warmup_complete=True))
        codes = {r.code for r in reasons}
        assert NoTradeReasonCode.WARMUP_INCOMPLETE not in codes
