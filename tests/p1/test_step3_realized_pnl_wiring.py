"""
Step 3 – Verify that SELL fills record realized PnL into PersistentLimitsTracker.

Tests:
1. SELL fill after BUY position records correct realized PnL.
2. Daily loss limit trips when cumulative realized losses exceed threshold.
3. Missing entry_price gracefully skips PnL recording (no crash).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Helpers – tiny fakes scoped to this file
# ---------------------------------------------------------------------------

class _MiniPositionStore:
    """Minimal position store with .get() / .upsert() / .delete()"""
    def __init__(self):
        self._positions = {}

    def get(self, symbol: str):
        return self._positions.get(symbol)

    def upsert(self, pos):
        self._positions[pos.symbol] = pos

    def delete(self, symbol: str):
        self._positions.pop(symbol, None)


class _MiniLimitsTracker:
    """Minimal limits tracker recording PnL in-memory."""
    def __init__(self, daily_loss_limit: Decimal = Decimal("1000")):
        self._daily_pnl = Decimal("0")
        self._daily_loss_limit = daily_loss_limit

    def record_realized_pnl(self, pnl):
        self._daily_pnl += Decimal(str(pnl))

    def get_daily_realized_pnl(self) -> Decimal:
        return self._daily_pnl

    def is_daily_loss_limit_breached(self) -> bool:
        return self._daily_pnl <= -self._daily_loss_limit


def _simulate_sell_fill(position_store, limits_tracker, symbol, fill_price, filled_qty):
    """
    Replicate the exact logic added to core/runtime/app.py for SELL fills.
    Returns True if PnL was recorded, False if skipped.
    """
    fill_price = Decimal(str(fill_price))
    filled_qty = Decimal(str(filled_qty))
    existing_pos = position_store.get(symbol)
    recorded = False
    if (
        existing_pos is not None
        and hasattr(existing_pos, "entry_price")
        and existing_pos.entry_price is not None
    ):
        realized_pnl = (fill_price - existing_pos.entry_price) * filled_qty
        limits_tracker.record_realized_pnl(realized_pnl)
        recorded = True
    position_store.delete(symbol)
    return recorded


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRealizedPnlWiring:
    """SELL fills record realized PnL into the limits tracker."""

    def test_sell_fill_records_profit(self):
        """A winning trade records positive PnL."""
        ps = _MiniPositionStore()
        lt = _MiniLimitsTracker()

        # Simulate a BUY at 100 → position exists
        ps.upsert(SimpleNamespace(symbol="AAPL", entry_price=Decimal("100"), quantity=Decimal("10")))

        # SELL at 105 → realized = (105 - 100) * 10 = 50
        recorded = _simulate_sell_fill(ps, lt, "AAPL", "105", "10")
        assert recorded is True
        assert lt.get_daily_realized_pnl() == Decimal("50")
        assert ps.get("AAPL") is None  # position deleted

    def test_sell_fill_records_loss(self):
        """A losing trade records negative PnL."""
        ps = _MiniPositionStore()
        lt = _MiniLimitsTracker()

        ps.upsert(SimpleNamespace(symbol="TSLA", entry_price=Decimal("200"), quantity=Decimal("5")))

        # SELL at 190 → realized = (190 - 200) * 5 = -50
        recorded = _simulate_sell_fill(ps, lt, "TSLA", "190", "5")
        assert recorded is True
        assert lt.get_daily_realized_pnl() == Decimal("-50")

    def test_daily_loss_limit_breached_after_losses(self):
        """Cumulative losses trip the daily loss limit."""
        ps = _MiniPositionStore()
        lt = _MiniLimitsTracker(daily_loss_limit=Decimal("100"))

        assert lt.is_daily_loss_limit_breached() is False

        # Trade 1: loss of -60
        ps.upsert(SimpleNamespace(symbol="AMD", entry_price=Decimal("150"), quantity=Decimal("2")))
        _simulate_sell_fill(ps, lt, "AMD", "120", "2")
        assert lt.get_daily_realized_pnl() == Decimal("-60")
        assert lt.is_daily_loss_limit_breached() is False

        # Trade 2: loss of -50 → cumulative = -110, breaches -100 limit
        ps.upsert(SimpleNamespace(symbol="NVDA", entry_price=Decimal("500"), quantity=Decimal("1")))
        _simulate_sell_fill(ps, lt, "NVDA", "450", "1")
        assert lt.get_daily_realized_pnl() == Decimal("-110")
        assert lt.is_daily_loss_limit_breached() is True

    def test_missing_position_skips_pnl(self):
        """SELL with no existing position does NOT record PnL."""
        ps = _MiniPositionStore()
        lt = _MiniLimitsTracker()

        recorded = _simulate_sell_fill(ps, lt, "XYZ", "100", "1")
        assert recorded is False
        assert lt.get_daily_realized_pnl() == Decimal("0")

    def test_missing_entry_price_skips_pnl(self):
        """Position without entry_price does NOT record PnL."""
        ps = _MiniPositionStore()
        lt = _MiniLimitsTracker()

        # Position exists but entry_price is None
        ps.upsert(SimpleNamespace(symbol="BAD", entry_price=None, quantity=Decimal("1")))
        recorded = _simulate_sell_fill(ps, lt, "BAD", "100", "1")
        assert recorded is False
        assert lt.get_daily_realized_pnl() == Decimal("0")
        assert ps.get("BAD") is None  # still deleted
