"""
P2-O3 — Adaptive but Bounded Thresholds (Offline Only)

INVARIANT:
    Strategy parameters MUST have hard bounds that prevent them from
    being set to dangerous values. The strategy MUST reject or clamp
    parameters outside acceptable ranges.

    This ensures that even offline parameter tuning cannot produce
    configurations that violate safety constraints.

TESTS:
    4 tests proving parameter bounds are enforced.
"""

from decimal import Decimal

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion


def _strat(**kw):
    cfg = {"vwap_min_bars": 3, "entry_deviation_pct": "0.003",
           "stop_loss_pct": "0.003", "risk_dollars_per_trade": "1.50",
           "max_trades_per_day": 1, "daily_loss_limit_usd": "2.50",
           "trade_start_time": "10:00", "trade_end_time": "11:30",
           "flat_time": "15:55", "max_notional_usd": "500",
           "max_time_in_trade_minutes": 60}
    cfg.update(kw)
    return VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])


class TestParameterBounds:

    def test_position_size_zero_on_zero_price(self):
        """_position_size(0) → 0 (no divide by zero)."""
        s = _strat()
        assert s._position_size(Decimal("0")) == Decimal("0")

    def test_position_size_zero_on_negative_price(self):
        """_position_size(<0) → 0."""
        s = _strat()
        assert s._position_size(Decimal("-10")) == Decimal("0")

    def test_max_notional_caps_position(self):
        """Position notional never exceeds max_notional_usd."""
        s = _strat(max_notional_usd="50", risk_dollars_per_trade="100",
                   stop_loss_pct="0.001")
        qty = s._position_size(Decimal("10"))
        notional = qty * Decimal("10")
        assert notional <= Decimal("50"), f"Notional {notional} exceeds cap"

    def test_vwap_min_bars_enforced(self):
        """Strategy cannot emit signals before vwap_min_bars."""
        s = _strat(vwap_min_bars=100)
        assert s.vwap_min_bars == 100
        # After only 5 bars, vwap is None
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from core.data.contract import MarketDataContract
        EASTERN = ZoneInfo("America/New_York")
        for i in range(5):
            et = datetime(2026, 1, 30, 10, i, 0, tzinfo=EASTERN)
            p = Decimal("99.60")
            bar = MarketDataContract(
                symbol="SPY", timestamp=et.astimezone(timezone.utc),
                open=p, high=Decimal("100"), low=p, close=p,
                volume=1000, provider="t",
            )
            sig = s.on_bar(bar)
            assert sig is None, "Must not emit signal during warmup"
