"""
Tests for anti-pyramiding and trailing stops.

COVERAGE:
- AntiPyramidingGuardian (prevent averaging down)
- TrailingStopManager (profit harvesting, direction-aware)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.risk_management import (
    AntiPyramidingGuardian,
    PyramidingDecision,
    TrailingStopManager,
    StopTrigger
)


# ============================================================================
# ANTI-PYRAMIDING TESTS
# ============================================================================

class TestAntiPyramidingGuardian:
    """Test anti-pyramiding protection."""
    
    def test_initialization(self):
        """Test guardian initializes correctly."""
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0"),
            max_position_size_percent=Decimal("20.0")
        )
        
        assert guardian.max_pyramiding_loss == Decimal("0.0")
        assert guardian.max_position_size == Decimal("20.0")
    
    def test_first_entry_always_allowed(self):
        """Test first entry (no position) is always allowed."""
        guardian = AntiPyramidingGuardian()
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("0"),  # No position
            proposed_add_size=Decimal("5000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("180.00"),
            portfolio_value=Decimal("50000")
        )
        
        assert check.allowed
        assert check.decision == PyramidingDecision.ALLOWED
    
    def test_block_adding_to_losing_long(self):
        """Test blocking add to losing LONG position."""
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0")  # No losing adds
        )
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("175.00"),  # Down $5 (-2.78%)
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_LOSING
        assert check.current_pnl_percent < 0
    
    def test_block_adding_to_losing_short(self):
        """Test blocking add to losing SHORT position."""
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0")
        )
        
        check = guardian.check_pyramiding(
            symbol="TSLA",
            side="SHORT",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("200.00"),
            current_price=Decimal("210.00"),  # Up $10 (-5% for short)
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_LOSING
        assert check.current_pnl_percent < 0
    
    def test_allow_adding_to_winning_long(self):
        """Test allowing add to profitable LONG position."""
        guardian = AntiPyramidingGuardian()
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00"),  # Up $5 (+2.78%)
            portfolio_value=Decimal("50000")
        )
        
        assert check.allowed
        assert check.decision == PyramidingDecision.ALLOWED
        assert check.current_pnl_percent > 0
    
    def test_allow_adding_to_winning_short(self):
        """Test allowing add to profitable SHORT position."""
        guardian = AntiPyramidingGuardian()
        
        check = guardian.check_pyramiding(
            symbol="TSLA",
            side="SHORT",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("200.00"),
            current_price=Decimal("190.00"),  # Down $10 (+5% for short)
            portfolio_value=Decimal("50000")
        )
        
        assert check.allowed
        assert check.decision == PyramidingDecision.ALLOWED
        assert check.current_pnl_percent > 0
    
    def test_block_exceeding_max_position_size(self):
        """Test blocking when position would exceed max size."""
        guardian = AntiPyramidingGuardian(
            max_position_size_percent=Decimal("10.0")  # Max 10% per position
        )
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("4000"),
            proposed_add_size=Decimal("2000"),  # Total would be $6K = 12%
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00"),  # Profitable
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_MAX_SIZE
    
    def test_min_profit_threshold(self):
        """Test minimum profit requirement for pyramiding."""
        guardian = AntiPyramidingGuardian(
            min_profit_to_pyramid_percent=Decimal("2.0")  # Need +2% to add
        )
        
        # Profitable but below threshold
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("181.00"),  # +0.56% (below 2%)
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_THRESHOLD
    
    def test_position_tracking(self):
        """Test position state tracking."""
        guardian = AntiPyramidingGuardian()
        
        guardian.update_position(
            symbol="AAPL",
            side="LONG",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00")
        )
        
        position = guardian.get_position("AAPL")
        
        assert position is not None
        assert position.symbol == "AAPL"
        assert position.is_profitable()
        assert not position.is_losing()


# ============================================================================
# TRAILING STOP TESTS
# ============================================================================

class TestTrailingStopManager:
    """Test trailing stop manager."""
    
    def test_initialization(self):
        """Test manager initializes correctly."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        assert manager.default_trail_percent == Decimal("2.0")
        assert manager.default_activation_percent == Decimal("3.0")
    
    def test_add_long_position(self):
        """Test adding LONG position with trailing stop."""
        manager = TrailingStopManager()
        
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        stop = manager.get_active_stop("AAPL")
        
        assert stop is not None
        assert stop.symbol == "AAPL"
        assert stop.side == "LONG"
        assert stop.is_active is False  # Not active yet
    
    def test_add_short_position(self):
        """Test adding SHORT position with trailing stop."""
        manager = TrailingStopManager()
        
        manager.add_position(
            symbol="TSLA",
            side="SHORT",
            entry_price=Decimal("200.00"),
            quantity=Decimal("50")
        )
        
        stop = manager.get_active_stop("TSLA")
        
        assert stop is not None
        assert stop.side == "SHORT"
    
    def test_long_activation_on_profit(self):
        """Test LONG stop activates when profit threshold reached."""
        manager = TrailingStopManager(
            default_activation_percent=Decimal("3.0")  # Activate at +3%
        )
        
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Price rises to +4% (above threshold)
        check = manager.update_price("AAPL", Decimal("187.20"))  # +4%
        
        stop = manager.get_active_stop("AAPL")
        
        assert stop.is_active  # Should activate
        assert check.trigger == StopTrigger.NOT_TRIGGERED
    
    def test_short_activation_on_profit(self):
        """Test SHORT stop activates when profit threshold reached."""
        manager = TrailingStopManager(
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="TSLA",
            side="SHORT",
            entry_price=Decimal("200.00"),
            quantity=Decimal("50")
        )
        
        # Price drops to +4% profit for short
        check = manager.update_price("TSLA", Decimal("192.00"))  # -4% price = +4% profit
        
        stop = manager.get_active_stop("TSLA")
        
        assert stop.is_active
    
    def test_long_trailing_stop_calculation(self):
        """Test LONG trailing stop price calculation."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Activate stop
        manager.update_price("AAPL", Decimal("186.00"))  # +3.33%
        
        # Price rises to $190
        manager.update_price("AAPL", Decimal("190.00"))
        
        stop = manager.get_active_stop("AAPL")
        
        # Stop should be at $190 * 0.98 = $186.20
        expected_stop = Decimal("190.00") * Decimal("0.98")
        assert abs(stop.current_stop_price - expected_stop) < Decimal("0.01")
    
    def test_short_trailing_stop_calculation(self):
        """Test SHORT trailing stop price calculation."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="TSLA",
            side="SHORT",
            entry_price=Decimal("200.00"),
            quantity=Decimal("50")
        )
        
        # Activate stop
        manager.update_price("TSLA", Decimal("194.00"))  # -3% price = +3% profit
        
        # Price drops to $190
        manager.update_price("TSLA", Decimal("190.00"))
        
        stop = manager.get_active_stop("TSLA")
        
        # Stop should be at $190 * 1.02 = $193.80
        expected_stop = Decimal("190.00") * Decimal("1.02")
        assert abs(stop.current_stop_price - expected_stop) < Decimal("0.01")
    
    def test_long_stop_trigger(self):
        """Test LONG stop triggers on price drop."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Activate and set stop
        manager.update_price("AAPL", Decimal("186.00"))
        manager.update_price("AAPL", Decimal("190.00"))  # Stop at $186.20
        
        # Price drops below stop
        check = manager.update_price("AAPL", Decimal("186.00"))
        
        assert check.triggered
        assert check.trigger == StopTrigger.TRIGGERED
    
    def test_short_stop_trigger(self):
        """Test SHORT stop triggers on price rise."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="TSLA",
            side="SHORT",
            entry_price=Decimal("200.00"),
            quantity=Decimal("50")
        )
        
        # Activate and set stop
        manager.update_price("TSLA", Decimal("194.00"))
        manager.update_price("TSLA", Decimal("190.00"))  # Stop at $193.80
        
        # Price rises above stop
        check = manager.update_price("TSLA", Decimal("194.00"))
        
        assert check.triggered
    
    def test_long_highest_price_tracking(self):
        """Test LONG tracks highest price reached."""
        manager = TrailingStopManager()
        
        manager.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        manager.update_price("AAPL", Decimal("185.00"))
        manager.update_price("AAPL", Decimal("190.00"))
        manager.update_price("AAPL", Decimal("188.00"))  # Pullback
        
        stop = manager.get_active_stop("AAPL")
        
        assert stop.highest_price == Decimal("190.00")
    
    def test_short_lowest_price_tracking(self):
        """Test SHORT tracks lowest price reached."""
        manager = TrailingStopManager()
        
        manager.add_position(
            symbol="TSLA",
            side="SHORT",
            entry_price=Decimal("200.00"),
            quantity=Decimal("50")
        )
        
        manager.update_price("TSLA", Decimal("195.00"))
        manager.update_price("TSLA", Decimal("190.00"))
        manager.update_price("TSLA", Decimal("192.00"))  # Bounce
        
        stop = manager.get_active_stop("TSLA")
        
        assert stop.lowest_price == Decimal("190.00")
    
    def test_batch_price_updates(self):
        """Test updating multiple positions at once."""
        manager = TrailingStopManager()
        
        manager.add_position("AAPL", "LONG", Decimal("180.00"), Decimal("100"))
        manager.add_position("TSLA", "SHORT", Decimal("200.00"), Decimal("50"))
        
        results = manager.update_all_prices({
            "AAPL": Decimal("185.00"),
            "TSLA": Decimal("195.00")
        })
        
        assert "AAPL" in results
        assert "TSLA" in results


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestRiskProtectionIntegration:
    """Test anti-pyramiding and trailing stops working together."""
    
    def test_full_position_lifecycle(self):
        """Test complete position lifecycle with both protections."""
        # Initialize both systems
        anti_pyramid = AntiPyramidingGuardian()
        trailing_stop = TrailingStopManager()
        
        # Enter position
        entry_price = Decimal("180.00")
        initial_size = Decimal("5000")
        
        # Add trailing stop
        trailing_stop.add_position(
            symbol="AAPL",
            side="LONG",
            entry_price=entry_price,
            quantity=Decimal("100")
        )
        
        # Track in anti-pyramid
        anti_pyramid.update_position(
            symbol="AAPL",
            side="LONG",
            quantity=Decimal("100"),
            avg_entry_price=entry_price,
            current_price=entry_price
        )
        
        # Price moves up - trailing stop activates
        trailing_stop.update_price("AAPL", Decimal("186.00"))
        
        # Try to add more (pyramiding)
        pyramid_check = anti_pyramid.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=initial_size,
            proposed_add_size=Decimal("2000"),
            avg_entry_price=entry_price,
            current_price=Decimal("186.00"),
            portfolio_value=Decimal("50000")
        )
        
        # Should allow (profitable)
        assert pyramid_check.allowed
        
        # Price drops - stop triggers
        stop_check = trailing_stop.update_price("AAPL", Decimal("182.00"))
        
        # Verify we have both protections
        assert trailing_stop.get_stop_count() == 1
        assert anti_pyramid.get_position("AAPL") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
