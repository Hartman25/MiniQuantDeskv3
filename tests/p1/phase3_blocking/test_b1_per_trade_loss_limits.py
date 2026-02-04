"""
P3-B1 — Per-Trade Loss Limits

INVARIANT:
    The strategy MUST cap per-trade risk via position sizing:
      qty = risk_dollars / (price * stop_loss_pct)

    The RiskManager MUST reject trades that exceed position size limits.

    The NotionalPositionSizer MUST prevent oversized positions.

TESTS:
    5 tests covering per-trade risk capping.
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from strategies.vwap_micro_mean_reversion import VWAPMicroMeanReversion
from core.risk.sizing import NotionalPositionSizer
from core.risk.manager import RiskManager, RiskLimits


class TestPerTradeLossLimit:

    def test_position_size_caps_risk_dollars(self):
        """qty * price * stop_loss_pct ≈ risk_dollars."""
        cfg = {"vwap_min_bars": 3, "stop_loss_pct": "0.003",
               "risk_dollars_per_trade": "1.50", "max_notional_usd": "500",
               "max_time_in_trade_minutes": 60}
        s = VWAPMicroMeanReversion(name="t", config=cfg, symbols=["SPY"])

        price = Decimal("100")
        qty = s._position_size(price)
        max_loss = qty * price * Decimal("0.003")
        assert max_loss <= Decimal("1.55"), f"Max loss {max_loss} exceeds risk budget"

    def test_notional_sizer_caps_exposure(self):
        """NotionalPositionSizer caps per-position exposure."""
        sizer = NotionalPositionSizer(
            max_exposure_per_position=Decimal("0.10"),
        )
        shares = sizer.calculate_position_size(
            account_value=Decimal("200"),
            current_price=Decimal("580"),
            existing_exposure_pct=Decimal("0"),
        )
        notional = shares * Decimal("580")
        assert notional <= Decimal("200") * Decimal("0.10") + Decimal("1")


class TestRiskManagerRejectsOversize:

    def _make_risk_mgr(self, max_pos_usd="100"):
        pos_store = MagicMock()
        pos_store.get_all.return_value = []
        pos_store.get.return_value = None
        limits = RiskLimits(
            max_position_size_usd=Decimal(max_pos_usd),
            max_position_pct_portfolio=Decimal("1.0"),
            max_portfolio_exposure_usd=Decimal("999999"),
            min_buying_power_reserve=Decimal("0"),
        )
        return RiskManager(position_store=pos_store, limits=limits)

    def test_rejects_oversized_trade(self):
        """Trade value > max_position_size_usd → rejected."""
        from core.brokers import BrokerOrderSide
        rm = self._make_risk_mgr(max_pos_usd="50")
        result = rm.validate_trade(
            symbol="SPY", quantity=Decimal("10"), side=BrokerOrderSide.BUY,
            price=Decimal("100"), account_value=Decimal("10000"),
            buying_power=Decimal("10000"),
        )
        assert not result.approved

    def test_approves_small_trade(self):
        """Trade value within limits → approved."""
        from core.brokers import BrokerOrderSide
        rm = self._make_risk_mgr(max_pos_usd="5000")
        result = rm.validate_trade(
            symbol="SPY", quantity=Decimal("1"), side=BrokerOrderSide.BUY,
            price=Decimal("100"), account_value=Decimal("10000"),
            buying_power=Decimal("10000"),
        )
        assert result.approved

    def test_risk_check_result_has_reasons(self):
        """Rejection includes human-readable reason."""
        from core.brokers import BrokerOrderSide
        rm = self._make_risk_mgr(max_pos_usd="10")
        result = rm.validate_trade(
            symbol="SPY", quantity=Decimal("10"), side=BrokerOrderSide.BUY,
            price=Decimal("100"), account_value=Decimal("10000"),
            buying_power=Decimal("10000"),
        )
        assert len(result.reasons) > 0
