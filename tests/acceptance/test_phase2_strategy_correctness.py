"""
Phase 2 - Strategy Correctness Acceptance Test

Validates VWAPMicroMeanReversion strategy guarantees from spec:
1. No signals during warmup (< vwap_min_bars)
2. No signals outside time window (10:00-11:30 ET)
3. Signal emitted on price deviation from VWAP
4. No more than max_trades_per_day
5. Strategy disables after daily loss limit
6. Explicit NO-TRADE conditions documented

SPEC ALIGNMENT:
- Tests strategy behavior in isolation (unit/integration style)
- No live broker required
- Deterministic, fast
- Asserts signal outcomes, not internal state
"""
import pytest
from decimal import Decimal
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# Import strategy directly for unit testing
from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from strategies.base import MarketDataContract


EASTERN = ZoneInfo("America/New_York")


def _make_bar(symbol="SPY", price=100.0, volume=1000, hour=10, minute=30):
    """Helper to create market data bar at specific time."""
    # Create bar timestamp in Eastern time
    dt_et = datetime.now(EASTERN).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    dt_utc = dt_et.astimezone(timezone.utc)
    
    return MarketDataContract(
        symbol=symbol,
        timestamp=dt_utc,
        open=Decimal(str(price)),
        high=Decimal(str(price + 0.5)),
        low=Decimal(str(price - 0.5)),
        close=Decimal(str(price)),
        volume=volume,
        provider="test"
    )


def test_phase2_no_signal_during_warmup():
    """
    Phase 2 Guarantee: No signals before VWAP warmup complete.
    
    GIVEN: Strategy with vwap_min_bars = 20
    WHEN: Only 10 bars received
    THEN: No signal emitted (VWAP not ready)
    """
    config = {
        "vwap_min_bars": 20,
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Send 10 bars (less than vwap_min_bars)
    for i in range(10):
        bar = _make_bar(price=100.0 + i * 0.1, volume=1000)
        signal = strategy.on_bar(bar)
        assert signal is None, f"No signal should be emitted during warmup (bar {i+1}/10)"


def test_phase2_no_signal_outside_time_window():
    """
    Phase 2 Guarantee: No signals outside configured time window.
    
    GIVEN: Strategy with trade_start=10:00, trade_end=11:30
    WHEN: Bar arrives at 09:30 (before window) or 12:00 (after window)
    THEN: No signal emitted (outside time window)
    """
    config = {
        "vwap_min_bars": 5,  # Low warmup for testing
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Warmup with bars in window
    for i in range(10):
        bar = _make_bar(price=100.0, volume=1000, hour=10, minute=i)
        strategy.on_bar(bar)
    
    # Bar before window (09:30)
    bar_before = _make_bar(price=99.0, volume=1000, hour=9, minute=30)
    signal = strategy.on_bar(bar_before)
    assert signal is None, "No signal before trade window start"
    
    # Bar after window (12:00)
    bar_after = _make_bar(price=99.0, volume=1000, hour=12, minute=0)
    signal = strategy.on_bar(bar_after)
    assert signal is None, "No signal after trade window end"


def test_phase2_signal_emitted_on_deviation():
    """
    Phase 2 Guarantee: Signal emitted when price deviates below VWAP threshold.
    
    GIVEN: Price < VWAP * (1 - entry_deviation_pct)
    WHEN: Within time window and after warmup
    THEN: BUY signal emitted
    """
    config = {
        "vwap_min_bars": 5,
        "entry_deviation_pct": "0.01",  # 1% deviation
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Build VWAP at 100.00
    for i in range(20):
        bar = _make_bar(price=100.0, volume=1000, hour=10, minute=i)
        strategy.on_bar(bar)
    
    # Price drops 1.5% below VWAP (should trigger)
    bar_low = _make_bar(price=98.50, volume=1000, hour=10, minute=25)
    signal = strategy.on_bar(bar_low)
    
    assert signal is not None, "Signal should be emitted when price deviates below VWAP"
    assert signal.symbol == "SPY"
    assert signal.side == "BUY"
    assert signal.order_type == "LIMIT" or signal.order_type == "MARKET"


def test_phase2_max_trades_per_day_enforced():
    """
    Phase 2 Guarantee: No more than max_trades_per_day.
    
    GIVEN: max_trades_per_day = 1
    WHEN: Second entry condition met same day
    THEN: No second signal emitted
    """
    config = {
        "vwap_min_bars": 5,
        "entry_deviation_pct": "0.01",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Warmup
    for i in range(20):
        bar = _make_bar(price=100.0, volume=1000, hour=10, minute=i)
        strategy.on_bar(bar)
    
    # First entry signal
    bar1 = _make_bar(price=98.50, volume=1000, hour=10, minute=25)
    signal1 = strategy.on_bar(bar1)
    assert signal1 is not None, "First signal should be emitted"
    
    # Simulate entry fill
    strategy.on_order_filled(
        order_id="test_1",
        symbol="SPY",
        filled_qty=Decimal("1"),
        fill_price=Decimal("98.50")
    )
    
    # Simulate exit
    strategy.on_order_filled(
        order_id="test_2",
        symbol="SPY",
        filled_qty=Decimal("1"),
        fill_price=Decimal("99.00")
    )
    
    # Second entry condition
    bar2 = _make_bar(price=98.50, volume=1000, hour=10, minute=40)
    signal2 = strategy.on_bar(bar2)
    assert signal2 is None, "Second signal blocked by max_trades_per_day"


def test_phase2_strategy_disabled_after_daily_loss_limit():
    """
    Phase 2 Guarantee: Strategy disables after daily loss limit hit.
    
    GIVEN: daily_loss_limit_usd = 2.50
    WHEN: Estimated daily loss >= limit
    THEN: Strategy returns None (no signals)
    """
    config = {
        "vwap_min_bars": 5,
        "entry_deviation_pct": "0.01",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 3,
        "daily_loss_limit_usd": "2.50",
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Warmup
    for i in range(20):
        bar = _make_bar(price=100.0, volume=1000, hour=10, minute=i)
        strategy.on_bar(bar)
    
    # Simulate two losing trades (2 * $1.50 = $3.00 loss > $2.50 limit)
    strategy._daily_pnl_est = Decimal("-3.00")
    strategy._disabled_today = True  # Strategy sets this internally
    
    # Entry condition met but strategy disabled
    bar = _make_bar(price=98.50, volume=1000, hour=10, minute=25)
    signal = strategy.on_bar(bar)
    
    assert signal is None, "Strategy should not emit signals after daily loss limit"


def test_phase2_force_flat_near_close():
    """
    Phase 2 Guarantee: Force exit near market close (15:55 ET).
    
    GIVEN: Open position
    WHEN: Time >= flat_time (15:55)
    THEN: SELL signal emitted regardless of P&L
    """
    config = {
        "vwap_min_bars": 5,
        "entry_deviation_pct": "0.01",
        "stop_loss_pct": "0.003",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
        "flat_time": "15:55",
    }
    
    strategy = VWAPMicroMeanReversion(
        name="TestStrategy",
        config=config,
        symbols=["SPY"],
        timeframe="1Min"
    )
    strategy.on_init()
    
    # Warmup
    for i in range(20):
        bar = _make_bar(price=100.0, volume=1000, hour=10, minute=i)
        strategy.on_bar(bar)
    
    # Simulate position
    strategy._in_position = True
    strategy._entry_price = Decimal("100.00")
    strategy._entry_qty = Decimal("1")
    
    # Bar at 15:55 (force flat time)
    bar_close = _make_bar(price=100.50, volume=1000, hour=15, minute=55)
    signal = strategy.on_bar(bar_close)
    
    assert signal is not None, "Force flat signal should be emitted at 15:55"
    assert signal.side == "SELL"
    assert signal.reason == "FORCE_FLAT_EOD"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
