"""
Tests for anti-pyramiding and trailing stops.

COVERAGE:
- AntiPyramidingGuardian (pyramiding prevention)
- TrailingStopManager (profit harvesting)
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
    
    def test_allow_first_entry(self):
        """Test first entry is always allowed."""
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
    
    def test_block_losing_long(self):
        """Test blocking add to losing LONG position."""
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0")  # No averaging down
        )
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("175.00"),  # -2.78% loss
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_LOSING
        assert check.current_pnl_percent < 0
    
    def test_block_losing_short(self):
        """Test blocking add to losing SHORT position."""
        guardian = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0")
        )
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="SHORT",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00"),  # -2.78% loss for SHORT
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_LOSING
    
    def test_allow_winning_long(self):
        """Test allowing add to profitable LONG position."""
        guardian = AntiPyramidingGuardian()
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00"),  # +2.78% profit
            portfolio_value=Decimal("50000")
        )
        
        assert check.allowed
        assert check.decision == PyramidingDecision.ALLOWED
    
    def test_allow_winning_short(self):
        """Test allowing add to profitable SHORT position."""
        guardian = AntiPyramidingGuardian()
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="SHORT",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("175.00"),  # +2.78% profit for SHORT
            portfolio_value=Decimal("50000")
        )
        
        assert check.allowed
        assert check.decision == PyramidingDecision.ALLOWED
    
    def test_block_max_position_size(self):
        """Test blocking when max position size exceeded."""
        guardian = AntiPyramidingGuardian(
            max_position_size_percent=Decimal("10.0")
        )
        
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("4000"),  # 8%
            proposed_add_size=Decimal("2000"),      # Would be 12%
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.00"),  # Profitable
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_MAX_SIZE
    
    def test_min_profit_requirement(self):
        """Test minimum profit requirement to pyramid."""
        guardian = AntiPyramidingGuardian(
            min_profit_to_pyramid_percent=Decimal("2.0")  # Must be +2%
        )
        
        # Small profit (1%) - should block
        check = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("181.80"),  # +1% profit
            portfolio_value=Decimal("50000")
        )
        
        assert not check.allowed
        assert check.decision == PyramidingDecision.BLOCKED_THRESHOLD
        
        # Large profit (3%) - should allow
        check2 = guardian.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),
            proposed_add_size=Decimal("2000"),
            avg_entry_price=Decimal("180.00"),
            current_price=Decimal("185.40"),  # +3% profit
            portfolio_value=Decimal("50000")
        )
        
        assert check2.allowed
    
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
        assert position.side == "LONG"
        assert position.is_profitable()


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
        assert stop.side == "LONG"
        assert stop.entry_price == Decimal("180.00")
        assert not stop.is_active  # Not active until profit threshold
    
    def test_long_stop_activation(self):
        """Test LONG trailing stop activation after profit threshold."""
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
        
        # Price rises 5% - should activate
        check = manager.update_price("AAPL", Decimal("189.00"))
        
        stop = manager.get_active_stop("AAPL")
        assert stop.is_active
        assert check.trigger == StopTrigger.NOT_TRIGGERED
    
    def test_long_stop_trigger(self):
        """Test LONG trailing stop trigger."""
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
        
        # Price rises to $189 (+5%)
        manager.update_price("AAPL", Decimal("189.00"))
        
        # Stop should be at $189 * 0.98 = $185.22
        stop = manager.get_active_stop("AAPL")
        assert stop.is_active
        
        # Price drops to $185 - should trigger
        check = manager.update_price("AAPL", Decimal("185.00"))
        
        assert check.triggered
        assert check.trigger == StopTrigger.TRIGGERED
    
    def test_short_stop_activation(self):
        """Test SHORT trailing stop activation."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="AAPL",
            side="SHORT",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Price drops 5% - should activate
        check = manager.update_price("AAPL", Decimal("171.00"))
        
        stop = manager.get_active_stop("AAPL")
        assert stop.is_active
    
    def test_short_stop_trigger(self):
        """Test SHORT trailing stop trigger."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="AAPL",
            side="SHORT",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Price drops to $171 (-5%)
        manager.update_price("AAPL", Decimal("171.00"))
        
        # Stop should be at $171 * 1.02 = $174.42
        stop = manager.get_active_stop("AAPL")
        assert stop.is_active
        
        # Price rises to $175 - should trigger
        check = manager.update_price("AAPL", Decimal("175.00"))
        
        assert check.triggered
        assert check.trigger == StopTrigger.TRIGGERED
    
    def test_long_stop_trails_up(self):
        """Test LONG stop trails price upward."""
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
        
        # Price rises to $189 (+5%) - activates
        manager.update_price("AAPL", Decimal("189.00"))
        stop = manager.get_active_stop("AAPL")
        first_stop = stop.current_stop_price
        
        # Price rises to $195 - stop should trail up
        manager.update_price("AAPL", Decimal("195.00"))
        stop = manager.get_active_stop("AAPL")
        second_stop = stop.current_stop_price
        
        assert second_stop > first_stop  # Stop moved up
    
    def test_short_stop_trails_down(self):
        """Test SHORT stop trails price downward."""
        manager = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        manager.add_position(
            symbol="AAPL",
            side="SHORT",
            entry_price=Decimal("180.00"),
            quantity=Decimal("100")
        )
        
        # Price drops to $171 (-5%) - activates
        manager.update_price("AAPL", Decimal("171.00"))
        stop = manager.get_active_stop("AAPL")
        first_stop = stop.current_stop_price
        
        # Price drops to $165 - stop should trail down
        manager.update_price("AAPL", Decimal("165.00"))
        stop = manager.get_active_stop("AAPL")
        second_stop = stop.current_stop_price
        
        assert second_stop < first_stop  # Stop moved down
    
    def test_batch_price_updates(self):
        """Test updating multiple positions at once."""
        manager = TrailingStopManager()
        
        manager.add_position("AAPL", "LONG", Decimal("180.00"), Decimal("100"))
        manager.add_position("MSFT", "LONG", Decimal("380.00"), Decimal("50"))
        
        # Update both at once
        results = manager.update_all_prices({
            "AAPL": Decimal("185.00"),
            "MSFT": Decimal("390.00")
        })
        
        assert "AAPL" in results
        assert "MSFT" in results


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestRiskFeaturesIntegration:
    """Test anti-pyramiding and trailing stops working together."""
    
    def test_complete_trade_lifecycle(self):
        """Test complete trade with both protections."""
        # Initialize both systems
        pyramiding_guard = AntiPyramidingGuardian(
            max_pyramiding_loss_percent=Decimal("0.0")
        )
        
        trailing_stops = TrailingStopManager(
            default_trail_percent=Decimal("2.0"),
            default_activation_percent=Decimal("3.0")
        )
        
        # 1. Enter position
        entry_price = Decimal("180.00")
        quantity = Decimal("100")
        
        trailing_stops.add_position("AAPL", "LONG", entry_price, quantity)
        
        # 2. Price moves up - try to pyramid (should allow)
        current_price = Decimal("185.00")
        
        pyramid_check = pyramiding_guard.check_pyramiding(
            symbol="AAPL",
            side="LONG",
            current_position_size=Decimal("5000"),   # 10% of portfolio
            proposed_add_size=Decimal("3000"),       # Total would be 16%
            avg_entry_price=entry_price,
            current_price=current_price,
            portfolio_value=Decimal("50000")
        )
        
        assert pyramid_check.allowed  # Profitable, pyramiding OK
        
        # 3. Price continues up - trailing stop activates
        trailing_stops.update_price("AAPL", Decimal("189.00"))
        stop = trailing_stops.get_active_stop("AAPL")
        assert stop.is_active
        
        # 4. Price drops - trailing stop triggers
        stop_check = trailing_stops.update_price("AAPL", Decimal("185.00"))
        assert stop_check.triggered
        
        # 5. After exit, remove from both systems
        trailing_stops.remove_position("AAPL")
        pyramiding_guard.remove_position("AAPL")
        
        assert trailing_stops.get_active_stop("AAPL") is None
        assert pyramiding_guard.get_position("AAPL") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
