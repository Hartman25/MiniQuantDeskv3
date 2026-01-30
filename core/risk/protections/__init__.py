"""
Risk protections package - Unified circuit breaker system.

Protection system provides:
- StoplossGuard - Consecutive loss protection
- MaxDrawdownProtection - Drawdown limits  
- CooldownPeriod - Loss-based cooldowns
- TimeWindowProtection - Trading hours enforcement
- VolatilityProtection - Volatility spike protection
"""

# NEW: Manager-based protections (unified system)
from .manager import ProtectionManager, create_default_protections
from .base import Protection, GlobalProtection, SymbolProtection, ProtectionResult, ProtectionTrigger
from .stoploss_guard import StoplossGuard
from .max_drawdown import MaxDrawdownProtection
from .cooldown import CooldownPeriod
from .time_window import TimeWindowProtection
from .volatility import VolatilityProtection

__all__ = [
    'ProtectionManager',
    'create_default_protections',
    'Protection',
    'GlobalProtection',
    'SymbolProtection',
    'ProtectionResult',
    'ProtectionTrigger',
    'StoplossGuard',
    'MaxDrawdownProtection',
    'CooldownPeriod',
    'TimeWindowProtection',
    'VolatilityProtection',
]
