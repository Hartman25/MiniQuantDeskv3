"""
Protection base class and common types.

Protections are dynamic circuit breakers that prevent trading
when certain conditions are met (losing streak, drawdown, etc).

Different from PreTradeRiskGate:
- RiskGate: Validates each trade (position limits, capital, etc)
- Protections: Block ALL trading temporarily based on recent performance

Pattern stolen from: Freqtrade protections/
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ProtectionTrigger(Enum):
    """Why protection was triggered"""
    STOPLOSS_STREAK = "stoploss_streak"
    MAX_DRAWDOWN = "max_drawdown"
    LOW_PROFIT = "low_profit"
    COOLDOWN_PERIOD = "cooldown_period"
    CONSECUTIVE_LOSSES = "consecutive_losses"


@dataclass
class ProtectionResult:
    """Result from protection check"""
    is_protected: bool
    reason: Optional[str] = None
    until: Optional[datetime] = None
    trigger: Optional[ProtectionTrigger] = None
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class Protection(ABC):
    """
    Base protection class.
    
    All protections must implement:
    - check(): Returns ProtectionResult
    - reset(): Clears protection state
    """
    
    def __init__(self, name: str, enabled: bool = True):
        """
        Args:
            name: Protection identifier
            enabled: Whether protection is active
        """
        self.name = name
        self.enabled = enabled
        self._protected_until: Optional[datetime] = None
        self._last_trigger: Optional[ProtectionTrigger] = None
        
        logger.info(f"Protection initialized: {name} (enabled={enabled})")
    
    @abstractmethod
    def check(
        self,
        symbol: Optional[str] = None,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> ProtectionResult:
        """
        Check if trading should be protected/blocked.
        
        Args:
            symbol: Specific symbol to check (None = global)
            current_trades: Currently open trades
            completed_trades: Recently completed trades
            
        Returns:
            ProtectionResult indicating if protected
        """
        pass
    
    @abstractmethod
    def reset(self):
        """Reset protection state"""
        pass
    
    def _is_currently_protected(self) -> bool:
        """Check if still in protection period"""
        if not self._protected_until:
            return False
        
        now = datetime.now(timezone.utc)
        if now >= self._protected_until:
            # Protection expired
            logger.info(f"Protection expired: {self.name}")
            self._protected_until = None
            self._last_trigger = None
            return False
        
        return True
    
    def _trigger_protection(
        self,
        duration: timedelta,
        trigger: ProtectionTrigger,
        reason: str,
        metadata: Optional[Dict] = None
    ) -> ProtectionResult:
        """Activate protection"""
        self._protected_until = datetime.now(timezone.utc) + duration
        self._last_trigger = trigger
        
        logger.warning(
            f"Protection triggered: {self.name}",
            extra={
                'protection': self.name,
                'trigger': trigger.value,
                'reason': reason,
                'until': self._protected_until.isoformat(),
                'duration_minutes': duration.total_seconds() / 60,
                'metadata': metadata or {}
            }
        )
        
        return ProtectionResult(
            is_protected=True,
            reason=reason,
            until=self._protected_until,
            trigger=trigger,
            metadata=metadata or {}
        )
    
    def get_status(self) -> Dict:
        """Get current protection status"""
        is_protected = self._is_currently_protected()
        
        return {
            'name': self.name,
            'enabled': self.enabled,
            'is_protected': is_protected,
            'protected_until': self._protected_until.isoformat() if self._protected_until else None,
            'last_trigger': self._last_trigger.value if self._last_trigger else None,
            'seconds_remaining': (
                (self._protected_until - datetime.now(timezone.utc)).total_seconds()
                if is_protected
                else 0
            )
        }


class GlobalProtection(Protection):
    """
    Protection that applies globally (blocks all trading).
    
    Examples:
    - Max drawdown protection (account-wide)
    - Daily loss limit reached
    - System issues
    """
    
    def check(
        self,
        symbol: Optional[str] = None,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> ProtectionResult:
        """Global check (symbol ignored)"""
        if not self.enabled:
            return ProtectionResult(is_protected=False)
        
        if self._is_currently_protected():
            return ProtectionResult(
                is_protected=True,
                reason=f"Global protection active: {self.name}",
                until=self._protected_until,
                trigger=self._last_trigger
            )
        
        return self._check_impl(current_trades, completed_trades)
    
    @abstractmethod
    def _check_impl(
        self,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Implement actual check logic"""
        pass


class SymbolProtection(Protection):
    """
    Protection that applies per-symbol.
    
    Examples:
    - Stoploss guard (block trading specific symbol after losses)
    - Low profit pairs (block underperformers)
    - Cooldown for specific symbol
    """
    
    def __init__(self, name: str, enabled: bool = True):
        super().__init__(name, enabled)
        self._protected_symbols: Dict[str, datetime] = {}
    
    def check(
        self,
        symbol: Optional[str] = None,
        current_trades: Optional[List] = None,
        completed_trades: Optional[List] = None
    ) -> ProtectionResult:
        """Symbol-specific check"""
        if not self.enabled:
            return ProtectionResult(is_protected=False)
        
        if not symbol:
            raise ValueError(f"{self.name} requires symbol parameter")
        
        # Check if symbol is protected
        if symbol in self._protected_symbols:
            until = self._protected_symbols[symbol]
            now = datetime.now(timezone.utc)
            
            if now >= until:
                # Protection expired
                del self._protected_symbols[symbol]
                logger.info(f"Symbol protection expired: {self.name} - {symbol}")
            else:
                return ProtectionResult(
                    is_protected=True,
                    reason=f"Symbol {symbol} protected by {self.name}",
                    until=until,
                    trigger=self._last_trigger
                )
        
        return self._check_impl(symbol, current_trades, completed_trades)
    
    @abstractmethod
    def _check_impl(
        self,
        symbol: str,
        current_trades: Optional[List],
        completed_trades: Optional[List]
    ) -> ProtectionResult:
        """Implement actual check logic"""
        pass
    
    def _trigger_symbol_protection(
        self,
        symbol: str,
        duration: timedelta,
        trigger: ProtectionTrigger,
        reason: str,
        metadata: Optional[Dict] = None
    ) -> ProtectionResult:
        """Activate protection for specific symbol"""
        until = datetime.now(timezone.utc) + duration
        self._protected_symbols[symbol] = until
        self._last_trigger = trigger
        
        logger.warning(
            f"Symbol protection triggered: {self.name} - {symbol}",
            extra={
                'protection': self.name,
                'symbol': symbol,
                'trigger': trigger.value,
                'reason': reason,
                'until': until.isoformat(),
                'duration_minutes': duration.total_seconds() / 60,
                'metadata': metadata or {}
            }
        )
        
        return ProtectionResult(
            is_protected=True,
            reason=reason,
            until=until,
            trigger=trigger,
            metadata=metadata or {}
        )
    
    def reset(self):
        """Reset all symbol protections"""
        self._protected_symbols.clear()
        self._last_trigger = None
        logger.info(f"Protection reset: {self.name}")
    
    def get_status(self) -> Dict:
        """Get protection status for all symbols"""
        base_status = super().get_status()
        base_status['protected_symbols'] = {
            symbol: until.isoformat()
            for symbol, until in self._protected_symbols.items()
        }
        return base_status
