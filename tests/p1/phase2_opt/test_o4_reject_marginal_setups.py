"""
P2-O4 — Reject Marginal Setups to Reduce Overtrading

INVARIANT:
    The strategy MUST have multiple independent gates that filter
    out marginal trade setups. The combination of max_trades_per_day,
    daily_loss_limit, trade_window, and VWAP deviation threshold
    MUST prevent overtrading.

    The StrategyPerformanceTracker MUST auto-disable strategies that
    show persistent underperformance (marginal edge).

TESTS:
    3 tests proving marginal setups are rejected.
"""

from decimal import Decimal
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from core.data.contract import MarketDataContract
from core.strategies.performance_tracker import StrategyPerformanceTracker

EASTERN = ZoneInfo("America/New_York")


def _strat(**kw):
    cfg = {"vwap_min_bars": 3, "entry_deviation_pct": "0.003",
           "stop_loss_pct": "0.003", "risk_dollars_per_trade": "1.50",
           "max_trades_per_day": 1, "daily_loss_limit_usd": "2.50",
           "trade_start_time": "10:00", "trade_end_time": "11:30",
           "flat_time": "15:55", "max_notional_usd": "500",
           "max_time_in_trade_minutes": 60}
    cfg.update(kw)
    return VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])


def _bar(price, hour=10, minute=15):
    p = Decimal(str(price))
    et = datetime(2026, 1, 30, hour, minute, 0, tzinfo=EASTERN)
    return MarketDataContract(symbol="SPY", timestamp=et.astimezone(timezone.utc),
                              open=p, high=p, low=p, close=p, volume=1000, provider="t")


class TestRejectMarginalSetups:

    def test_narrow_window_limits_opportunities(self):
        """90-minute trade window caps maximum daily opportunities."""
        s = _strat(trade_start_time="10:00", trade_end_time="11:30",
                   max_trades_per_day=5)
        # Warmup
        for i in range(3):
            s.on_bar(_bar(100, hour=9, minute=31 + i))

        # Count signals across the 90 minute window (minutes 0-59 at hour 10, then 0-29 at hour 11)
        signals = 0
        for i in range(90):
            h = 10 + i // 60
            m = i % 60
            sig = s.on_bar(_bar(99.60, hour=h, minute=m))
            if sig is not None and sig.side == "BUY":
                signals += 1
                s.on_order_filled(f"O{i}", "SPY", sig.quantity, Decimal("99.60"))
                # Quick exit
                sig2 = s.on_bar(_bar(100, hour=h, minute=m))
                if sig2:
                    s.on_order_filled(f"X{i}", "SPY", sig2.quantity, Decimal("100"))

        # Max 5 trades per day
        assert signals <= 5

    def test_deviation_threshold_rejects_weak_signals(self):
        """Price barely below VWAP (within threshold) → no signal."""
        s = _strat(entry_deviation_pct="0.003")
        for i in range(3):
            s.on_bar(_bar(100, hour=9, minute=31 + i))

        # 0.1% below VWAP — less than 0.3% threshold
        sig = s.on_bar(_bar(99.90))
        assert sig is None

    def test_tracker_disables_persistent_losers(self):
        """StrategyPerformanceTracker disables after sustained losses."""
        t = StrategyPerformanceTracker(
            max_consecutive_losses=5,
            min_trades_for_evaluation=5,
            min_sharpe_ratio=Decimal("-999"),
            min_win_rate_percent=Decimal("0"),
            max_drawdown_percent=Decimal("999"),
        )
        base = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            t.record_trade(
                strategy_id="bad_strat", symbol="SPY", side="LONG",
                quantity=Decimal("10"), entry_price=Decimal("100"),
                exit_price=Decimal(str(99 - i * 0.1)),
                entry_time=base + timedelta(minutes=i * 10),
                exit_time=base + timedelta(minutes=i * 10 + 5),
            )
        assert not t.is_strategy_active("bad_strat")
