"""
Phase 2 conftest — strategy-level test helpers.

This conftest does NOT inherit collect_ignore from the root conftest.
Phase 2 tests run strategy logic directly (no runtime patching needed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, time, timedelta
from decimal import Decimal
from typing import Optional

import pytest


def make_bar(
    symbol: str = "SPY",
    timestamp: Optional[datetime] = None,
    open_: Decimal = Decimal("100.00"),
    high: Decimal = Decimal("100.10"),
    low: Decimal = Decimal("99.90"),
    close: Decimal = Decimal("100.00"),
    volume: int = 10000,
    provider: str = "test",
):
    """
    Create a MarketDataContract for testing.

    Uses the real MarketDataContract so we test against the actual validation.
    Falls back to a SimpleNamespace if import fails (keeps tests self-contained).
    """
    if timestamp is None:
        timestamp = datetime(2026, 1, 30, 15, 0, 0, tzinfo=timezone.utc)

    try:
        from core.data.contract import MarketDataContract
        return MarketDataContract(
            symbol=symbol,
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            provider=provider,
        )
    except Exception:
        from types import SimpleNamespace
        return SimpleNamespace(
            symbol=symbol.upper(),
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            provider=provider,
        )


def make_strategy(config_overrides: dict = None):
    """Create a VWAPMicroMeanReversion instance with test defaults."""
    from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion

    base_config = {
        "vwap_min_bars": 5,  # fast warmup for tests
        "entry_deviation_pct": "0.003",
        "stop_loss_pct": "0.003",
        "take_profit_pct": "0.0015",
        "risk_dollars_per_trade": "1.50",
        "max_trades_per_day": 1,
        "daily_loss_limit_usd": "2.50",
        "trade_start_time": "10:00",
        "trade_end_time": "11:30",
        "flat_time": "15:55",
        "max_notional_usd": "500",
    }
    if config_overrides:
        base_config.update(config_overrides)

    strat = VWAPMicroMeanReversion(
        name="test_vwap_micro",
        config=base_config,
        symbols=["SPY"],
        timeframe="1Min",
    )
    strat.on_init()
    return strat


@pytest.fixture
def strategy():
    """Default VWAPMicroMeanReversion strategy for tests."""
    return make_strategy()


@pytest.fixture
def bar_factory():
    """Factory fixture returning make_bar callable."""
    return make_bar
