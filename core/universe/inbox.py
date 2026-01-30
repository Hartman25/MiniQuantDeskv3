"""
Universe Inbox Processor (Gate 2)

Reads candidates from scanner (Gate 1) and applies trading bot filters.
Writes accept/reject decisions and maintains active universe snapshot.

FLOW:
1. Read new lines from inbox.jsonl (track offset in state.json)
2. For each candidate:
   - Apply reevaluation filters (spread, volume, price, ATR)
   - Check global rules (max trades, premarket watch-only, daily limits)
   - Write decision to decisions.jsonl
3. Update universe_active.json atomically
4. Purge expired symbols

FILES:
- data/universe/inbox.jsonl (input from scanner)
- data/universe/decisions.jsonl (output decisions)
- data/universe/universe_active.json (atomic snapshot)
- data/universe/state.json (internal state)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from core.logging import get_logger, LogStream
from core.time import Clock


logger = get_logger(LogStream.SYSTEM)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Core universe (always included)
CORE_UNIVERSE = ["SPY", "QQQ"]

# Max accepted symbols per day (prevents runaway universe growth)
MAX_ACCEPTED_PER_DAY = 10

# Filters (Gate 2 reevaluation)
MIN_SPREAD_BPS = 0       # Optional lower bound (generally keep at 0)
MAX_SPREAD_BPS = 50      # Reject if bid-ask spread > 50 bps
MIN_DOLLAR_VOLUME = 5_000_000   # $5M daily volume minimum
MIN_PRICE = Decimal("5.00")
MAX_PRICE = Decimal("500.00")
MIN_ATR_RATIO = Decimal("0.5")  # ATR should be at least 0.5% of price

# Expiry: symbols expire at end of next trading day
SYMBOL_EXPIRY_HOURS = 24  # Legacy fallback; prefer next-trading-day close


def _next_trading_day_close_utc(now_utc: datetime) -> datetime:
    """Return 16:00 America/New_York on the next trading day (weekends only).
    Note: This does not account for US market holidays.
    """
    et = ZoneInfo('America/New_York')
    now_et = now_utc.astimezone(et)
    # Next trading day date
    d = now_et.date()
    # move to next day
    d = d + timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat,6=Sun
        d = d + timedelta(days=1)
    close_et = datetime(d.year, d.month, d.day, 16, 0, 0, tzinfo=et)
    return close_et.astimezone(timezone.utc)



@dataclass
class ScannerCandidate:
    """Candidate from scanner (Gate 1)"""
    id: str
    ts: datetime
    symbol: str
    session: str  # "pre" or "rth"
    score: float
    features: Dict[str, float]
    levels: Dict[str, float]
    source: str
    version: str

    @classmethod
    def from_json(cls, data: Dict) -> "ScannerCandidate":
        """Parse from inbox.jsonl line"""
        return cls(
            id=data["id"],
            ts=datetime.fromisoformat(data["ts"].replace("Z", "+00:00")),
            symbol=data["symbol"],
            session=data.get("session", "rth"),
            score=float(data.get("score", 0)),
            features=data.get("features", {}),
            levels=data.get("levels", {}),
            source=data.get("source", "scanner"),
            version=data.get("version", "1.0"),
        )


@dataclass
class Decision:
    """Accept/reject decision (Gate 2 output)"""
    id: str
    ts: datetime
    symbol: str
    decision: str  # "accept" or "reject"
    expires: Optional[datetime]
    reason: str
    bot_version: str = "v1"

    def to_json(self) -> Dict:
        """Serialize for decisions.jsonl"""
        return {
            "id": self.id,
            "ts": self.ts.isoformat().replace("+00:00", "Z"),
            "symbol": self.symbol,
            "decision": self.decision,
            "expires": self.expires.isoformat().replace("+00:00", "Z") if self.expires else None,
            "reason": self.reason,
            "bot_version": self.bot_version,
        }


@dataclass
class UniverseState:
    """Internal state tracking"""
    inbox_offset: int = 0
    last_processed_id: Optional[str] = None
    daily_accept_count: int = 0
    daily_accept_date: str = ""
    version: str = "1.0"

    @classmethod
    def load(cls, path: Path) -> "UniverseState":
        """Load from state.json"""
        if not path.exists():
            return cls()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(
                inbox_offset=data.get("inbox_offset", 0),
                last_processed_id=data.get("last_processed_id"),
                daily_accept_count=data.get("daily_accept_count", 0),
                daily_accept_date=data.get("daily_accept_date", ""),
                version=data.get("version", "1.0"),
            )
        except Exception as e:
            logger.warning(f"Failed to load state.json: {e}, using defaults")
            return cls()

    def save(self, path: Path) -> None:
        """Save to state.json"""
        data = {
            "inbox_offset": self.inbox_offset,
            "last_processed_id": self.last_processed_id,
            "daily_accept_count": self.daily_accept_count,
            "daily_accept_date": self.daily_accept_date,
            "version": self.version,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


class UniverseInboxProcessor:
    """
    Gate 2: Reevaluates scanner candidates and maintains active universe.
    
    FILTERS:
    - Spread check
    - Dollar volume check
    - Price range check
    - ATR sanity check
    - Strategy compatibility
    
    GLOBAL RULES:
    - One trade at a time (if in position, reject new)
    - Premarket = watch only (accept candidates, don't trade)
    - Max accepted per day
    - Symbol expiry (end of next trading day)
    """

    def __init__(
        self,
        data_dir: Path,
        clock: Clock,
        broker_connector=None,  # Optional: for live price checks
    ):
        self.data_dir = data_dir
        self.clock = clock
        self.broker = broker_connector

        self.inbox_path = data_dir / "inbox.jsonl"
        self.decisions_path = data_dir / "decisions.jsonl"
        self.universe_path = data_dir / "universe_active.json"
        self.state_path = data_dir / "state.json"

        # Ensure files exist
        self.inbox_path.touch(exist_ok=True)
        self.decisions_path.touch(exist_ok=True)

        # Load state
        self.state = UniverseState.load(self.state_path)

        # Track recently seen symbols (dedup within 5 minutes)
        self.recent_symbols: Dict[str, datetime] = {}

    def process_new_candidates(
        self,
        has_open_position: bool = False,
        has_open_orders: bool = False,
    ) -> List[Decision]:
        """
        Read new candidates from inbox and apply Gate 2 filters.
        
        Args:
            has_open_position: True if bot currently in a position
            has_open_orders: True if bot has pending orders
            
        Returns:
            List of Decision objects
        """
        decisions: List[Decision] = []

        # Reset daily counter if new day
        today = self.clock.now().strftime("%Y-%m-%d")
        if self.state.daily_accept_date != today:
            self.state.daily_accept_count = 0
            self.state.daily_accept_date = today

        # Read new lines from inbox
        candidates = self._read_new_inbox_lines()

        for candidate in candidates:
            # Update state
            self.state.last_processed_id = candidate.id

            # Check if symbol recently processed (dedup)
            if self._is_recently_seen(candidate.symbol):
                logger.debug(f"Skipping {candidate.symbol} (recently seen)")
                continue

            # Apply filters
            decision = self._evaluate_candidate(
                candidate,
                has_open_position=has_open_position,
                has_open_orders=has_open_orders,
            )

            decisions.append(decision)

            # Write decision to decisions.jsonl
            self._append_decision(decision)

            # Update recent symbols
            self.recent_symbols[candidate.symbol] = self.clock.now()

        # Save state
        self.state.save(self.state_path)

        # Rebuild universe snapshot (atomic)
        if decisions:
            self._rebuild_universe_snapshot()

        return decisions

    def _read_new_inbox_lines(self) -> List[ScannerCandidate]:
        """Read new lines from inbox.jsonl starting from last offset.

        Advances self.state.inbox_offset by the number of *raw lines* consumed, so a malformed
        line cannot permanently block processing.
        """
        candidates: List[ScannerCandidate] = []
        lines_consumed = 0

        try:
            with open(self.inbox_path, "r") as f:
                # Skip to last offset
                for _ in range(self.state.inbox_offset):
                    f.readline()

                # Read new lines
                for raw in f:
                    lines_consumed += 1
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        data = json.loads(line)
                        candidate = ScannerCandidate.from_json(data)
                        candidates.append(candidate)
                    except Exception as e:
                        logger.error(f"Failed to parse inbox line: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to read inbox.jsonl: {e}")

        # Advance offset by raw lines consumed
        self.state.inbox_offset += lines_consumed
        return candidates

    def _is_recently_seen(self, symbol: str) -> bool:
        """Check if symbol was processed recently (within 5 minutes)"""
        if symbol not in self.recent_symbols:
            return False

        last_seen = self.recent_symbols[symbol]
        elapsed = (self.clock.now() - last_seen).total_seconds()
        return elapsed < 300  # 5 minutes

    def _evaluate_candidate(
        self,
        candidate: ScannerCandidate,
        has_open_position: bool,
        has_open_orders: bool,
    ) -> Decision:
        """
        Apply Gate 2 filters to candidate.
        
        Returns Decision with accept/reject.
        """
        now = self.clock.now()
        symbol = candidate.symbol

        # Global rule: One trade at a time
        if has_open_position or has_open_orders:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason="bot_busy_one_trade_at_a_time",
            )

        # Global rule: Max accepted per day
        if self.state.daily_accept_count >= MAX_ACCEPTED_PER_DAY:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason=f"daily_limit_reached_{MAX_ACCEPTED_PER_DAY}",
            )

        # Check spread (from features)
        spread_bps = float(candidate.features.get("spread_bps", 0) or 0)
        if spread_bps > 0 and spread_bps > MAX_SPREAD_BPS:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason=f"spread_filter_failed_{spread_bps:.1f}bps",
            )

        # Check dollar volume (from features)
        dollar_vol = float(candidate.features.get("dollar_vol", 0) or 0)
        if dollar_vol > 0 and dollar_vol < MIN_DOLLAR_VOLUME:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason=f"dollar_volume_too_low_{dollar_vol/1e6:.1f}M",
            )

        # Check price range (from levels or features)
        price_val = (candidate.features.get("price") or candidate.features.get("last") or candidate.features.get("last_price")
                     or candidate.levels.get("break") or candidate.levels.get("hold") or 0)
        price = Decimal(str(price_val))
        if price < MIN_PRICE or price > MAX_PRICE:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason=f"price_out_of_range_{price}",
            )

        # Check ATR ratio (optional, if available)
        atr_pct = float(candidate.features.get("atr_pct", 0) or 0)
        if atr_pct > 0 and Decimal(str(atr_pct)) < MIN_ATR_RATIO:
            return Decision(
                id=candidate.id,
                ts=now,
                symbol=symbol,
                decision="reject",
                expires=None,
                reason=f"atr_too_low_{atr_pct:.2f}pct",
            )

        # All filters passed - ACCEPT
        expires = _next_trading_day_close_utc(now)

        # Increment daily counter
        self.state.daily_accept_count += 1

        return Decision(
            id=candidate.id,
            ts=now,
            symbol=symbol,
            decision="accept",
            expires=expires,
            reason=f"passed_all_filters_score_{candidate.score:.1f}",
        )

    def _append_decision(self, decision: Decision) -> None:
        """Append decision to decisions.jsonl"""
        try:
            with open(self.decisions_path, "a") as f:
                f.write(json.dumps(decision.to_json()) + "\n")
        except Exception as e:
            logger.error(f"Failed to write decision: {e}")

    def _rebuild_universe_snapshot(self) -> None:
        """
        Rebuild universe_active.json atomically.
        
        Reads all decisions.jsonl, filters expired, writes atomic snapshot.
        """
        now = self.clock.now()

        # Read all accepted symbols from decisions.jsonl
        accepted: Set[str] = set()
        expires_by_symbol: Dict[str, str] = {}

        try:
            with open(self.decisions_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("decision") != "accept":
                            continue

                        symbol = data["symbol"]
                        expires_str = data.get("expires")
                        if not expires_str:
                            continue

                        expires_dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))

                        # Check if expired
                        if expires_dt < now:
                            logger.debug(f"Symbol {symbol} expired at {expires_str}")
                            continue

                        # Add to active universe
                        accepted.add(symbol)
                        expires_by_symbol[symbol] = expires_str

                    except Exception as e:
                        logger.error(f"Failed to parse decision line: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to read decisions.jsonl: {e}")

        # Build universe snapshot
        universe_data = {
            "core": CORE_UNIVERSE,
            "accepted": sorted(list(accepted)),
            "expires_by_symbol": expires_by_symbol,
            "last_updated": now.isoformat().replace("+00:00", "Z"),
            "version": "1.0",
        }

        # Atomic write (write to temp, then rename)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                delete=False,
                dir=self.data_dir,
                suffix=".tmp",
            ) as f:
                temp_path = f.name
                json.dump(universe_data, f, indent=2)

            # Atomic rename
            os.replace(temp_path, self.universe_path)

            logger.info(
                f"Universe updated: core={len(CORE_UNIVERSE)} "
                f"accepted={len(accepted)} total={len(CORE_UNIVERSE) + len(accepted)}"
            )

        except Exception as e:
            logger.error(f"Failed to write universe snapshot: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def get_active_universe(self) -> List[str]:
        """
        Load active universe from universe_active.json.
        
        Returns:
            List of symbols (core + accepted)
        """
        try:
            with open(self.universe_path, "r") as f:
                data = json.load(f)
            
            core = data.get("core", [])
            accepted = data.get("accepted", [])
            
            # Combine and deduplicate
            universe = list(set(core + accepted))
            return sorted(universe)

        except Exception as e:
            logger.error(f"Failed to load universe: {e}, using core only")
            return CORE_UNIVERSE.copy()

    def purge_expired_symbols(self) -> None:
        """Force rebuild of universe (purges expired symbols)"""
        self._rebuild_universe_snapshot()
