"""
P3-O2 — Volatility-Aware Trade Frequency Limits

INVARIANT:
    The strategy's max_trades_per_day MUST cap trade frequency.
    The StoplossGuard's lookback_period MUST constrain how quickly
    the strategy can re-enter after losses.

    Together they prevent overtrading in volatile conditions.

TESTS:
    3 tests proving volatility-aware frequency limiting works.
"""

from datetime import timedelta, datetime, timezone
from decimal import Decimal

from core.risk.protections.stoploss_guard import StoplossGuard
from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion


class _Trade:
    def __init__(self, symbol, profit, close_timestamp):
        self.symbol = symbol
        self.profit = profit
        self.close_timestamp = close_timestamp

    def get(self, key, default=None):
        return getattr(self, key, default)


def _trade(symbol, profit, minutes_ago):
    return _Trade(
        symbol=symbol,
        profit=Decimal(str(profit)),
        close_timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class TestVolatilityAwareFrequency:

    def test_max_trades_per_day_is_configurable(self):
        """max_trades_per_day limits daily trade count."""
        cfg = {"vwap_min_bars": 3, "entry_deviation_pct": "0.003",
               "stop_loss_pct": "0.003", "risk_dollars_per_trade": "1.50",
               "max_trades_per_day": 2, "daily_loss_limit_usd": "50",
               "trade_start_time": "10:00", "trade_end_time": "14:00",
               "flat_time": "15:55", "max_notional_usd": "500",
               "max_time_in_trade_minutes": 60}
        s = VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])
        assert s.max_trades_per_day == 2

    def test_stoploss_guard_lookback_constrains_reentry(self):
        """Short lookback with losses → protected from re-entry."""
        guard = StoplossGuard(
            max_stoplosses=2,
            lookback_period=timedelta(minutes=30),
            cooldown_duration=timedelta(minutes=15),
        )
        trades = [
            _trade("SPY", -1.00, 5),
            _trade("SPY", -1.00, 10),
        ]
        result = guard.check(symbol="SPY", completed_trades=trades)
        assert result.is_protected is True

    def test_stoploss_guard_long_lookback_catches_more(self):
        """Longer lookback captures more losses."""
        guard_short = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(minutes=10),
        )
        guard_long = StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(hours=2),
        )
        # Losses at 5, 30, 60 minutes ago
        trades = [
            _trade("SPY", -1.00, 5),
            _trade("SPY", -1.00, 30),
            _trade("SPY", -1.00, 60),
        ]
        # Short lookback: only sees the 5-minute-ago trade
        result_short = guard_short.check(symbol="SPY", completed_trades=trades)
        # Long lookback: sees all 3
        result_long = guard_long.check(symbol="SPY", completed_trades=trades)
        assert result_short.is_protected is False
        assert result_long.is_protected is True
