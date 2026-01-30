from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_trade_id(symbol: str, strategy: str, *, prefix: str = "T") -> str:
    # Example: T-SPY-VWAPMicroMeanReversion-20260128T040512Z-8f3c2a9d
    u = uuid.uuid4().hex[:8]
    sym = (symbol or "UNK").upper()
    strat = (strategy or "Strategy").replace(" ", "")
    return f"{prefix}-{sym}-{strat}-{_utc_compact()}-{u}"


def new_order_id(*, prefix: str = "O") -> str:
    # Example: O-20260128T040512Z-2b6f6e1c
    return f"{prefix}-{_utc_compact()}-{uuid.uuid4().hex[:8]}"


def new_event_id(*, prefix: str = "E") -> str:
    return f"{prefix}-{_utc_compact()}-{uuid.uuid4().hex[:8]}"


def get_run_id(env_var: str = "RUN_ID") -> str:
    rid = os.getenv(env_var)
    if rid:
        return rid
    return f"RUN-{_utc_compact()}-{uuid.uuid4().hex[:6]}"
