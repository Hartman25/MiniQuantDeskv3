"""
Protection Manager

Coordinates all protections and provides unified checking.

Pattern: Freqtrade ProtectionManager
"""

from typing import List, Optional, Dict
import logging

from .base import Protection, ProtectionResult
from .stoploss_guard import StoplossGuard
from .max_drawdown import MaxDrawdownProtection
from .cooldown import CooldownPeriod
from .time_window import TimeWindowProtection
from .volatility import VolatilityProtection

logger = logging.getLogger(__name__)


class ProtectionManager:
    """
    Manages all protections and provides unified interface.
    
    Usage:
        manager = ProtectionManager()
        manager.add_protection(StoplossGuard(max_stoplosses=3))
        manager.add_protection(MaxDrawdownProtection(max_drawdown=0.15))
        manager.add_protection(CooldownPeriod(loss_threshold=500))
        
        # Check before trading
        result = manager.check(
            symbol='SPY',
            current_trades=current,
            completed_trades=completed
        )
        
        if result.is_protected:
            logger.warning(f"Trading blocked: {result.reason}")
            # Don't trade
    """
    
    def __init__(self):
        self._protections: List[Protection] = []
        logger.info("ProtectionManager initialized")
    
    def add_protection(self, protection: Protection):
        """Add protection"""
        self._protections.append(protection)
        logger.info(
            f"Added protection: {protection.name} (enabled={protection.enabled})"
        )
    
    def remove_protection(self, name: str):
        """Remove protection by name"""
        self._protections = [
            p for p in self._protections
            if p.name != name
        ]
        logger.info(f"Removed protection: {name}")
    
    def check(
        self,
        symbol: Optional[str] = None,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> ProtectionResult:
        """
        Check all protections.
        
        Returns first protection that blocks trading.
        If multiple protections trigger, returns the first one.
        
        Args:
            symbol: Symbol to check (None for global)
            current_trades: Currently open trades
            completed_trades: Recently completed trades
            
        Returns:
            ProtectionResult (is_protected=True if blocked)
        """
        for protection in self._protections:
            if not protection.enabled:
                continue
            
            try:
                result = protection.check(
                    symbol=symbol,
                    current_trades=current_trades,
                    completed_trades=completed_trades
                )
                
                if result.is_protected:
                    logger.warning(
                        f"Protection triggered: {protection.name}",
                        extra={
                            'protection': protection.name,
                            'symbol': symbol,
                            'reason': result.reason,
                            'until': result.until.isoformat() if result.until else None
                        }
                    )
                    return result
                    
            except Exception as e:
                logger.error(
                    f"Protection check failed: {protection.name}",
                    extra={'error': str(e), 'protection': protection.name},
                    exc_info=True
                )
        
        return ProtectionResult(is_protected=False)
    
    def is_symbol_protected(
        self,
        symbol: str,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> bool:
        """
        Quick check if symbol is protected.
        
        Returns:
            True if symbol should not be traded
        """
        result = self.check(
            symbol=symbol,
            current_trades=current_trades,
            completed_trades=completed_trades
        )
        return result.is_protected
    
    def is_globally_protected(
        self,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> bool:
        """
        Quick check if all trading is protected.
        
        Returns:
            True if no trading should occur
        """
        result = self.check(
            symbol=None,
            current_trades=current_trades,
            completed_trades=completed_trades
        )
        return result.is_protected
    
    def reset_all(self):
        """Reset all protections"""
        for protection in self._protections:
            protection.reset()
        logger.info("All protections reset")
    
    def get_all_statuses(self) -> List[Dict]:
        """Get status of all protections"""
        return [
            protection.get_status()
            for protection in self._protections
        ]
    
    def enable_protection(self, name: str):
        """Enable protection by name"""
        for protection in self._protections:
            if protection.name == name:
                protection.enabled = True
                logger.info(f"Enabled protection: {name}")
                return
        logger.warning(f"Protection not found: {name}")
    
    def disable_protection(self, name: str):
        """Disable protection by name"""
        for protection in self._protections:
            if protection.name == name:
                protection.enabled = False
                logger.info(f"Disabled protection: {name}")
                return
        logger.warning(f"Protection not found: {name}")


def create_default_protections() -> ProtectionManager:
    """
    Create protection manager with ALL protections.
    
    Returns:
        ProtectionManager with 5 protections configured:
        1. StoplossGuard - Consecutive loss protection
        2. MaxDrawdownProtection - Drawdown limits
        3. CooldownPeriod - Loss-based cooldowns
        4. TimeWindowProtection - Trading hours (10:00-11:30 ET)
        5. VolatilityProtection - Volatility spike protection
    """
    from datetime import timedelta, time
    from decimal import Decimal
    
    manager = ProtectionManager()
    
    # 1. Stoploss guard: 3 consecutive losses = 1 hour cooldown
    manager.add_protection(
        StoplossGuard(
            max_stoplosses=3,
            lookback_period=timedelta(hours=1),
            cooldown_duration=timedelta(hours=1),
            enabled=True
        )
    )
    
    # 2. Max drawdown: 15% drawdown = 24 hour stop
    manager.add_protection(
        MaxDrawdownProtection(
            max_drawdown=0.15,
            cooldown_duration=timedelta(hours=24),
            lookback_period=timedelta(days=7),
            enabled=True
        )
    )
    
    # 3. Cooldown: $500 loss = 30 minute pause
    manager.add_protection(
        CooldownPeriod(
            loss_threshold=Decimal('500'),
            cooldown_duration=timedelta(minutes=30),
            enabled=True
        )
    )
    
    # 4. Time window: Only trade 10:00-11:30 ET (CRITICAL for VWAP micro strategy)
    manager.add_protection(
        TimeWindowProtection(
            start_time=time(10, 0),
            end_time=time(11, 30),
            timezone_str="America/New_York",
            enabled=True
        )
    )
    
    # 5. Volatility: Block when std > 0.6% (protects micro accounts)
    manager.add_protection(
        VolatilityProtection(
            max_std=Decimal("0.006"),  # 0.6%
            min_points=20,
            lookback=60,
            enabled=True
        )
    )
    
    logger.info("Default protections configured (5 total)")
    return manager
