import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.data.contract import MarketDataContract
from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion


def bar(ts_utc: datetime, close: Decimal, symbol="SPY", vol=1000):
    # simple OHLC around close
    return MarketDataContract(
        symbol=symbol,
        timestamp=ts_utc,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=vol,
        provider="test"
    )


def utc_from_et(hh, mm, day=1):
    # Jan 2, 2026 is a trading day; use that.
    # ET is UTC-5 in winter.
    base = datetime(2026, 1, 2, hh+5, mm, tzinfo=timezone.utc)
    return base


def make_strategy(**cfg):
    base_cfg = {
        "vwap_min_bars": 20,
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.0",
        "max_trades_per_day": 1,
        "daily_loss_limit_usd": "2.0",
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
        "flat_time": "15:55",
        "max_notional_usd": "50",
    }
    base_cfg.update(cfg)
    s = VWAPMicroMeanReversion(
        name="VWAPMicroMeanReversion",
        config=base_cfg,
        symbols=["SPY"],
        timeframe="1Min",
    )
    s.on_init()
    return s


def seed_vwap(strategy, start_ts, price=Decimal("100.00"), n=20):
    # seed with constant price so vwap ~ price
    ts = start_ts
    for i in range(n):
        sig = strategy.on_bar(bar(ts, price))
        assert sig is None
        ts += timedelta(minutes=1)
    return ts


def test_no_signal_during_warmup():
    s = make_strategy(vwap_min_bars=30)
    ts = utc_from_et(10, 0)
    # 29 bars -> still warming up
    for _ in range(29):
        assert s.on_bar(bar(ts, Decimal("100.0"))) is None
        ts += timedelta(minutes=1)


def test_no_signal_outside_time_window():
    s = make_strategy()
    ts = utc_from_et(9, 30)  # 09:30 ET (outside 10:00-11:30)
    ts2 = seed_vwap(s, ts, price=Decimal("100.00"), n=20)
    sig = s.on_bar(bar(ts2, Decimal("99.0")))  # would be below vwap
    assert sig is None


def test_signal_emitted_on_deviation():
    s = make_strategy(entry_deviation_pct="0.003")
    ts = utc_from_et(10, 0)
    ts2 = seed_vwap(s, ts, price=Decimal("100.00"), n=20)
    sig = s.on_bar(bar(ts2, Decimal("99.50")))  # 0.5% below vwap
    assert sig is not None
    assert sig.side == "BUY"
    assert sig.symbol == "SPY"
    assert sig.quantity > 0


def test_no_more_than_one_entry_per_day():
    s = make_strategy(max_trades_per_day=1)
    ts = utc_from_et(10, 0)
    ts2 = seed_vwap(s, ts, price=Decimal("100.00"), n=20)
    sig1 = s.on_bar(bar(ts2, Decimal("99.50")))
    assert sig1 is not None and sig1.side == "BUY"
    # simulate fill -> now in position
    s.on_order_filled("o1", "SPY", sig1.quantity, Decimal("99.50"))

    # another bar still below VWAP should NOT produce another BUY (in position)
    ts3 = ts2 + timedelta(minutes=1)
    sig2 = s.on_bar(bar(ts3, Decimal("99.40")))
    assert sig2 is None or sig2.side != "BUY"


def test_strategy_disables_after_daily_loss_limit():
    # Make it easy to hit limit: 1.0 risk per stop, limit=1.0 => after 1 stop, disable
    s = make_strategy(risk_dollars_per_trade="1.0", daily_loss_limit_usd="1.0")
    ts = utc_from_et(10, 0)
    ts2 = seed_vwap(s, ts, price=Decimal("100.00"), n=20)

    # Enter
    entry = s.on_bar(bar(ts2, Decimal("99.50")))
    assert entry is not None and entry.side == "BUY"
    s.on_order_filled("e1", "SPY", entry.quantity, Decimal("99.50"))

    # Trigger stop (price drops below entry*(1-stop_loss_pct))
    ts3 = ts2 + timedelta(minutes=1)
    stop_sig = s.on_bar(bar(ts3, Decimal("99.00")))
    assert stop_sig is not None
    assert stop_sig.side == "SELL"
    assert stop_sig.reason == "STOP_LOSS"
    s.on_order_filled("x1", "SPY", stop_sig.quantity, Decimal("99.00"))

    # After stop, strategy should be disabled for the day -> no new entries
    ts4 = ts3 + timedelta(minutes=1)
    sig = s.on_bar(bar(ts4, Decimal("99.50")))
    assert sig is None
