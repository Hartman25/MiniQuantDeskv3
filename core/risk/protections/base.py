from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class ProtectionTrigger(Enum):
    """Events that can trigger protection checks."""
    PRE_TRADE = "pre_trade"
    PRE_ENTRY = "pre_entry"
    PRE_EXIT = "pre_exit"
    POST_TRADE = "post_trade"
    ON_BAR = "on_bar"
    ON_TICK = "on_tick"
    DAILY = "daily"
    # Legacy triggers from old protections system
    STOPLOSS_STREAK = "stoploss_streak"
    MAX_DRAWDOWN = "max_drawdown"
    LOW_PROFIT = "low_profit"
    COOLDOWN_PERIOD = "cooldown_period"
    CONSECUTIVE_LOSSES = "consecutive_losses"


@dataclass(frozen=True)
class ProtectionContext:
    today: Optional[date] = None
    account_value: Decimal = Decimal("0")
    equity_start_of_day: Optional[Decimal] = None
    now: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # If today not provided but now is, derive today from now
        if self.today is None and self.now is not None:
            object.__setattr__(self, 'today', self.now.date())
        # If today still None, use current date
        if self.today is None:
            object.__setattr__(self, 'today', date.today())

    @property
    def extra(self) -> Dict[str, Any]:
        return self.metadata

@dataclass(frozen=True)
class ProtectionDecision:
    """Return value from a protection check.

    allow=True means trading can proceed.
    allow=False blocks; reason should be short and machine-friendly.
    """
    allow: bool
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProtectionResult:
    """Result from protection check (backwards-compatible with old API)."""
    is_protected: bool
    reason: Optional[str] = None
    until: Optional[datetime] = None
    trigger: Optional[ProtectionTrigger] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Backwards compatibility with new API (allowed = not is_protected)
    @property
    def allowed(self) -> bool:
        return not self.is_protected
    
    # Support old list-based API
    reasons: List[str] = field(default_factory=list)
    triggered: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        # Sync reason to reasons list if provided
        if self.reason and self.reason not in self.reasons:
            self.reasons.append(self.reason)
        # Sync trigger to triggered list if provided
        if self.trigger and self.trigger.value not in self.triggered:
            self.triggered.append(self.trigger.value)

    @classmethod
    def allow(cls) -> "ProtectionResult":
        return cls(is_protected=False)

    @classmethod
    def block(
        cls,
        reason: str,
        *,
        triggered: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ProtectionResult":
        return cls(
            is_protected=True,
            reason=reason,
            metadata=metadata or {},
        )

    def add_block(
        self,
        reason: str,
        *,
        triggered: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.is_protected = True
        if reason:
            self.reason = reason
            if reason not in self.reasons:
                self.reasons.append(reason)
        if triggered and triggered not in self.triggered:
            self.triggered.append(triggered)
        if metadata:
            self.metadata.update(metadata)

    def add_allow(self, *, metadata: Optional[Dict[str, Any]] = None) -> None:
        if metadata:
            self.metadata.update(metadata)


class BaseProtection(ABC):
    """Base class for protections."""

    name: str = "BaseProtection"
    triggers: List[ProtectionTrigger] = [ProtectionTrigger.PRE_TRADE]

    def __init__(self) -> None:
        self.logger = logger

    def should_run(self, trigger: ProtectionTrigger) -> bool:
        return trigger in set(self.triggers)

    @abstractmethod
    def check(
        self,
        *,
        trigger: ProtectionTrigger,
        ctx: Optional[ProtectionContext] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> ProtectionDecision:
        raise NotImplementedError


# --- Backwards-compatible export expected by core.risk.protections.manager ---
# Note: Protection is now defined as a standalone class below (see end of file)
# This provides better backwards compatibility with the legacy API.


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def floor_to_day_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def is_same_utc_day(a: datetime, b: datetime) -> bool:
    return floor_to_day_utc(a) == floor_to_day_utc(b)


# ==============================================================================
# BACKWARDS-COMPATIBLE PROTECTION CLASSES (Legacy API)
# ==============================================================================

class Protection(ABC):
    """
    Base protection class (legacy API for backwards compatibility).
    
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
        duration,  # timedelta
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
    
    def reset(self):
        """Reset protection state"""
        self._protected_until = None
        self._last_trigger = None
        logger.info(f"Protection reset: {self.name}")


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
        duration,  # timedelta
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
