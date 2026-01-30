"""
Protection system for preventing trading during adverse conditions.

Protections are dynamic circuit breakers that block trading when:
- Too many consecutive losses (StoplossGuard)
- Maximum drawdown exceeded (MaxDrawdownProtection)
- Major loss occurred (CooldownPeriod)

Pattern: Freqtrade protections/

Usage:
    from core.risk.protections import ProtectionManager, create_default_protections
    
    # Use defaults
    manager = create_default_protections()
    
    # Or custom
    from core.risk.protections import (
        ProtectionManager,
        StoplossGuard,
        MaxDrawdownProtection,
        CooldownPeriod
    )
    
    manager = ProtectionManager()
    manager.add_protection(StoplossGuard(max_stoplosses=3))
    manager.add_protection(MaxDrawdownProtection(max_drawdown=0.15))
    
    # Check before trading
    if manager.is_symbol_protected('SPY', completed_trades=trades):
        # Don't trade SPY
        pass
"""

from .base import (
    Protection,
    GlobalProtection,
    SymbolProtection,
    ProtectionResult,
    ProtectionTrigger
)
from .stoploss_guard import StoplossGuard
from .max_drawdown import MaxDrawdownProtection
from .cooldown import CooldownPeriod
from .manager import ProtectionManager, create_default_protections

__all__ = [
    # Base classes
    'Protection',
    'GlobalProtection',
    'SymbolProtection',
    'ProtectionResult',
    'ProtectionTrigger',
    
    # Specific protections
    'StoplossGuard',
    'MaxDrawdownProtection',
    'CooldownPeriod',
    
    # Manager
    'ProtectionManager',
    'create_default_protections'
]
