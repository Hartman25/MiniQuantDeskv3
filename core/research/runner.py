"""
Deterministic Research Runner.

PATCH 14 — Provides a sealed, reproducible environment for strategy research.

ARCHITECTURE:
    ResearchRunner wraps:
      - BacktestClock   (deterministic time)
      - NullExecution   (deterministic fills, no broker)
      - Coordinator     (pure signal evaluation)

    Every run with the same config + data + seed produces identical results.
    All decisions are captured in a journal-ready event list for analysis.

USAGE:
    runner = ResearchRunner(seed=42)
    runner.add_bar(bar_dict)
    runner.add_bar(bar_dict)
    report = runner.finalize()

    # report.decisions  → list of all SignalDecision events
    # report.fills      → list of all fill events
    # report.journal    → full audit trail
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.time.clock import BacktestClock, ensure_utc


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchFill:
    """Record of a simulated fill."""
    bar_index: int
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    order_id: str

    def to_dict(self) -> dict:
        return {
            "event": "research_fill",
            "bar_index": self.bar_index,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "timestamp": self.timestamp.isoformat(),
            "order_id": self.order_id,
        }


@dataclass(frozen=True)
class ResearchDecision:
    """Record of one signal evaluation decision."""
    bar_index: int
    symbol: str
    action: str          # "SUBMIT_MARKET", "SUBMIT_LIMIT", "SKIP", "NO_SIGNAL"
    reason: Optional[str] = None
    detail: str = ""
    quantity: Decimal = Decimal("0")
    side: str = ""
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = {
            "event": "research_decision",
            "bar_index": self.bar_index,
            "symbol": self.symbol,
            "action": self.action,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
        if self.reason:
            d["reason"] = self.reason
        if self.detail:
            d["detail"] = self.detail
        if self.quantity:
            d["quantity"] = str(self.quantity)
        if self.side:
            d["side"] = self.side
        return d


@dataclass
class ResearchReport:
    """
    Complete results of a research run.

    Immutable once finalized; every field is a snapshot.
    """
    seed: int
    bar_count: int
    decisions: List[ResearchDecision]
    fills: List[ResearchFill]
    journal: List[dict]
    start_time: datetime
    end_time: datetime
    config_hash: str

    @property
    def fill_count(self) -> int:
        return len(self.fills)

    @property
    def skip_count(self) -> int:
        return sum(1 for d in self.decisions if d.action == "SKIP")

    @property
    def submit_count(self) -> int:
        return sum(1 for d in self.decisions if d.action.startswith("SUBMIT"))

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "bar_count": self.bar_count,
            "fill_count": self.fill_count,
            "skip_count": self.skip_count,
            "submit_count": self.submit_count,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "config_hash": self.config_hash,
            "decisions": [d.to_dict() for d in self.decisions],
            "fills": [f.to_dict() for f in self.fills],
        }


# ---------------------------------------------------------------------------
# Research Runner
# ---------------------------------------------------------------------------

class ResearchRunner:
    """
    Deterministic research runner for strategy evaluation.

    Guarantees:
      1. Same seed + bars + strategy → identical results.
      2. No network I/O, no real broker, no side effects.
      3. Every decision logged for post-hoc analysis.
      4. Reproducible random number generation via explicit seed.
    """

    def __init__(
        self,
        *,
        seed: int = 0,
        strategy_fn: Optional[Callable] = None,
        start_time: Optional[datetime] = None,
        bar_interval: timedelta = timedelta(minutes=1),
        fill_price_source: str = "close",
    ) -> None:
        """
        Args:
            seed: RNG seed for reproducibility.
            strategy_fn: Callable(bar_dict) → Optional[signal_dict].
                If None, bars are recorded but no signals generated.
            start_time: Simulation start time (UTC).  Defaults to
                2025-01-02 09:30:00 UTC.
            bar_interval: Time step per bar.
            fill_price_source: Which bar field to use as fill price
                ("close", "open", "vwap").
        """
        self._seed = seed
        self._rng = random.Random(seed)

        st = start_time or datetime(2025, 1, 2, 14, 30, 0, tzinfo=timezone.utc)
        self._clock = BacktestClock(ensure_utc(st))
        self._bar_interval = bar_interval
        self._fill_price_source = fill_price_source

        self._strategy_fn = strategy_fn

        # State
        self._bars: List[dict] = []
        self._decisions: List[ResearchDecision] = []
        self._fills: List[ResearchFill] = []
        self._journal: List[dict] = []
        self._order_counter = 0
        self._finalized = False

        # Config hash for reproducibility verification
        config_blob = f"seed={seed}|bar_interval={bar_interval}|fill={fill_price_source}"
        self._config_hash = hashlib.sha256(config_blob.encode()).hexdigest()[:16]

        self._journal.append({
            "event": "research_run_started",
            "seed": seed,
            "config_hash": self._config_hash,
            "start_time": self._clock.now().isoformat(),
        })

    # -- Properties ----------------------------------------------------------

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def bar_count(self) -> int:
        return len(self._bars)

    @property
    def decisions(self) -> List[ResearchDecision]:
        return list(self._decisions)

    @property
    def fills(self) -> List[ResearchFill]:
        return list(self._fills)

    @property
    def clock(self) -> BacktestClock:
        return self._clock

    @property
    def config_hash(self) -> str:
        return self._config_hash

    @property
    def rng(self) -> random.Random:
        """Expose the seeded RNG for strategies that need randomness."""
        return self._rng

    # -- Bar processing ------------------------------------------------------

    def add_bar(self, bar: dict) -> ResearchDecision:
        """
        Feed one bar into the research runner.

        1. Records bar in history.
        2. Advances the clock.
        3. Calls strategy_fn (if set) to get a signal.
        4. Evaluates the signal and records decision.
        5. If signal is actionable, records a simulated fill.

        Returns:
            The ResearchDecision for this bar.
        """
        if self._finalized:
            raise RuntimeError("Runner is finalized; create a new instance.")

        idx = len(self._bars)
        self._bars.append(bar)

        now = self._clock.now()
        symbol = bar.get("symbol", "UNKNOWN")

        # Get signal from strategy
        signal = None
        if self._strategy_fn is not None:
            signal = self._strategy_fn(bar)

        if signal is None:
            decision = ResearchDecision(
                bar_index=idx,
                symbol=symbol,
                action="NO_SIGNAL",
                timestamp=now,
            )
        else:
            side = signal.get("side", "BUY")
            qty = Decimal(str(signal.get("quantity", 0)))
            order_type = signal.get("order_type", "MARKET")

            if qty <= 0:
                decision = ResearchDecision(
                    bar_index=idx,
                    symbol=symbol,
                    action="SKIP",
                    reason="qty_zero",
                    quantity=qty,
                    side=side,
                    timestamp=now,
                )
            else:
                action = "SUBMIT_LIMIT" if order_type == "LIMIT" else "SUBMIT_MARKET"
                decision = ResearchDecision(
                    bar_index=idx,
                    symbol=symbol,
                    action=action,
                    quantity=qty,
                    side=side,
                    timestamp=now,
                )

                # Simulate fill
                fill_price = Decimal(str(
                    bar.get(self._fill_price_source, bar.get("close", 100))
                ))
                self._order_counter += 1
                order_id = f"RES-{self._seed}-{self._order_counter}"

                fill = ResearchFill(
                    bar_index=idx,
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    price=fill_price,
                    timestamp=now,
                    order_id=order_id,
                )
                self._fills.append(fill)
                self._journal.append(fill.to_dict())

        self._decisions.append(decision)
        self._journal.append(decision.to_dict())

        # Advance clock for next bar
        self._clock.advance(self._bar_interval)

        return decision

    # -- Finalization --------------------------------------------------------

    def finalize(self) -> ResearchReport:
        """
        Seal the run and produce a ResearchReport.

        After calling finalize(), no more bars can be added.
        """
        if self._finalized:
            raise RuntimeError("Already finalized.")

        self._finalized = True
        end_time = self._clock.now()

        start_event = self._journal[0]
        start_time = datetime.fromisoformat(start_event["start_time"])

        self._journal.append({
            "event": "research_run_completed",
            "bar_count": len(self._bars),
            "fill_count": len(self._fills),
            "end_time": end_time.isoformat(),
        })

        return ResearchReport(
            seed=self._seed,
            bar_count=len(self._bars),
            decisions=list(self._decisions),
            fills=list(self._fills),
            journal=list(self._journal),
            start_time=start_time,
            end_time=end_time,
            config_hash=self._config_hash,
        )

    # -- Utility -------------------------------------------------------------

    def reset(self) -> "ResearchRunner":
        """
        Create a fresh runner with the same config (for re-run comparison).

        Returns a NEW instance; the current one remains immutable if finalized.
        """
        return ResearchRunner(
            seed=self._seed,
            strategy_fn=self._strategy_fn,
            start_time=datetime.fromisoformat(self._journal[0]["start_time"]),
            bar_interval=self._bar_interval,
            fill_price_source=self._fill_price_source,
        )
