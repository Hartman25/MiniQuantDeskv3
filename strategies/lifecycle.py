"""
Strategy Lifecycle Manager - Coordinates strategy execution.

Responsibilities:
- Start/stop strategies
- Route market data to strategies
- Route order events to strategies
- Manage enabled/disabled state
- Normalize typed StrategySignal -> dict for downstream consumers
"""

from __future__ import annotations

from typing import Dict, List, Optional
from decimal import Decimal
import logging

from core.data.contract import MarketDataContract
from strategies.base import IStrategy

try:
    from strategies.signals import StrategySignal
except Exception:  # pragma: no cover
    StrategySignal = None  # type: ignore

logger = logging.getLogger(__name__)


class StrategyLifecycleManager:
    """Coordinates strategy execution lifecycle."""

    def __init__(self):
        self._strategies: Dict[str, IStrategy] = {}
        self._enabled: set[str] = set()
        logger.info("StrategyLifecycleManager initialized")

    def add_strategy(self, strategy: IStrategy) -> None:
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy {strategy.name} already exists")
        self._strategies[strategy.name] = strategy
        logger.info("Added strategy: %s", strategy.name)

    def start_strategy(self, name: str) -> None:
        s = self._strategies.get(name)
        if not s:
            raise ValueError(f"Strategy {name} not found")
        s.on_init()
        s.enabled = True
        self._enabled.add(name)
        logger.info("Started strategy: %s", name)

    def stop_strategy(self, name: str) -> None:
        s = self._strategies.get(name)
        if not s:
            return
        s.on_stop()
        s.enabled = False
        self._enabled.discard(name)
        logger.info("Stopped strategy: %s", name)

    def on_bar(self, bar: MarketDataContract) -> List[Dict]:
        """
        Route bar to enabled strategies.
        Returns a list of legacy dict signals (normalized).
        """
        out: List[Dict] = []

        for name in list(self._enabled):
            s = self._strategies.get(name)
            if not s or not s.enabled:
                continue

            if bar.symbol not in s.symbols:
                continue

            try:
                sig = s.on_bar(bar)
                s.bars_processed += 1

                if not sig:
                    continue

                # Normalize typed -> dict
                if StrategySignal is not None and isinstance(sig, StrategySignal):
                    sig = sig.to_dict()

                if not isinstance(sig, dict):
                    logger.warning("Strategy %s produced unsupported signal type: %s", s.name, type(sig))
                    continue

                s.signals_generated += 1
                out.append(sig)

                logger.info(
                    "[%s] signal: %s %s %s",
                    s.name,
                    sig.get("side"),
                    sig.get("quantity"),
                    sig.get("symbol"),
                )

            except Exception:
                logger.exception("Error in strategy %s", name)

        return out

    def on_order_filled(self, strategy_name: str, order_id: str, symbol: str, filled_qty: Decimal, fill_price: Decimal) -> Optional[Dict]:
        s = self._strategies.get(strategy_name)
        if not s:
            return None
        res = s.on_order_filled(order_id, symbol, filled_qty, fill_price)
        if StrategySignal is not None and isinstance(res, StrategySignal):
            return res.to_dict()
        return res if isinstance(res, dict) else None

    def on_order_rejected(self, strategy_name: str, order_id: str, symbol: str, reason: str) -> Optional[Dict]:
        s = self._strategies.get(strategy_name)
        if not s:
            return None
        res = s.on_order_rejected(order_id, symbol, reason)
        if StrategySignal is not None and isinstance(res, StrategySignal):
            return res.to_dict()
        return res if isinstance(res, dict) else None

    # --- Compatibility helpers (so other modules don't break) ---

    def get_strategy(self, name: str) -> Optional[IStrategy]:
        return self._strategies.get(name)

    def get_strategies(self) -> Dict[str, IStrategy]:
        return self._strategies.copy()

    def list_strategies(self) -> List[str]:
        return list(self._strategies.keys())

    def get_enabled_strategies(self) -> List[str]:
        return list(self._enabled)

    def list_enabled_strategies(self) -> List[str]:
        return list(self._enabled)
