from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_trade_id(symbol: str | None = None, strategy: str | None = None, *, prefix: str = "T") -> str:
    """
    Stable per-trade lifecycle correlation ID.

    Examples:
      T-SPY-VWAPMicroMeanReversion-20260128T040512Z-8f3c2a9d
      T-UNK-Strategy-20260128T040512Z-8f3c2a9d
    """
    u = uuid.uuid4().hex[:8]
    sym = (symbol or "UNK").upper()
    strat = (strategy or "Strategy").replace(" ", "")
    return f"{prefix}-{sym}-{strat}-{_utc_compact()}-{u}"


def new_internal_order_id(*, prefix: str = "O") -> str:
    """Stable per order intent correlation ID."""
    return f"{prefix}-{_utc_compact()}-{uuid.uuid4().hex[:8]}"


# Backwards-compat alias (some call-sites historically used new_order_id)
def new_order_id(*, prefix: str = "O") -> str:
    return new_internal_order_id(prefix=prefix)


def new_event_id(*, prefix: str = "E") -> str:
    return f"{prefix}-{_utc_compact()}-{uuid.uuid4().hex[:8]}"


def get_run_id(env_var: str = "RUN_ID") -> str:
    rid = os.getenv(env_var)
    if rid:
        return rid
    return f"RUN-{_utc_compact()}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class CorrelationIds:
    trade_id: str
    internal_order_id: str
    broker_order_id: str | None = None
