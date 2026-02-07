"""
Config discipline enforcement layer.

PATCH 15 — Provides structured validation that:
  1. Rejects unknown keys (strict mode).
  2. Returns all errors at once (not just the first).
  3. Detects config mutation after load (freeze guard).
  4. Produces human-readable error reports.

USAGE:
    result = validate_config(raw_dict)
    if not result.ok:
        for e in result.errors:
            print(e)

    frozen = freeze_config(validated_schema)
    # frozen.risk.daily_loss_limit_usd = 0  → raises
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigError:
    """One config validation error with location and message."""
    path: str           # dot-separated path, e.g. "risk.daily_loss_limit_usd"
    message: str        # human-readable error
    error_type: str     # "missing", "type_error", "value_error", "extra_key"

    def __str__(self) -> str:
        return f"[{self.error_type}] {self.path}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """
    Complete result of config validation.

    ok=True means zero errors.  Always inspect .errors for details.
    """
    ok: bool
    errors: Tuple[ConfigError, ...]
    warnings: Tuple[str, ...] = ()

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def summary(self) -> str:
        if self.ok:
            return "Config OK (0 errors)"
        lines = [f"Config INVALID ({self.error_count} errors):"]
        for e in self.errors:
            lines.append(f"  • {e}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Known top-level and nested keys (for extra-key detection)
# ---------------------------------------------------------------------------

_KNOWN_TOP_KEYS = frozenset({
    "risk", "broker", "data", "strategies", "session", "logging",
    "position_db_path", "transaction_log_path", "limit_tracker_path",
})

_KNOWN_RISK_KEYS = frozenset({
    "initial_account_value", "max_open_positions", "max_position_size_pct",
    "daily_loss_limit_usd", "weekly_loss_limit_usd", "risk_per_trade_pct",
    "circuit_breaker_enabled", "circuit_breaker_loss_pct", "halt_duration_minutes",
})

_KNOWN_BROKER_KEYS = frozenset({
    "name", "api_key", "api_secret", "base_url", "paper_trading",
})

_KNOWN_DATA_KEYS = frozenset({
    "primary_provider", "fallback_providers", "max_staleness_seconds",
    "cache_enabled", "cache_dir",
})

_KNOWN_SESSION_KEYS = frozenset({
    "cycle_interval_seconds", "max_daily_trades",
    "trading_hours_only", "startup_recovery_enabled",
})

_KNOWN_LOGGING_KEYS = frozenset({
    "log_dir", "log_level", "console_level", "json_logs",
    "max_bytes", "backup_count",
})

_KNOWN_STRATEGY_KEYS = frozenset({
    "name", "enabled", "symbols", "timeframe", "lookback_bars", "parameters",
})

_SECTION_KEYS = {
    "risk": _KNOWN_RISK_KEYS,
    "broker": _KNOWN_BROKER_KEYS,
    "data": _KNOWN_DATA_KEYS,
    "session": _KNOWN_SESSION_KEYS,
    "logging": _KNOWN_LOGGING_KEYS,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_extra_keys(raw: Dict[str, Any]) -> List[ConfigError]:
    """
    Detect unknown keys at top level and within known sections.

    Returns a list of ConfigError(error_type="extra_key") for each.
    """
    errors: List[ConfigError] = []

    # Top-level extras
    for key in raw:
        if key not in _KNOWN_TOP_KEYS:
            errors.append(ConfigError(
                path=key,
                message=f"Unknown top-level key '{key}'",
                error_type="extra_key",
            ))

    # Section-level extras
    for section, known in _SECTION_KEYS.items():
        sub = raw.get(section)
        if isinstance(sub, dict):
            for key in sub:
                if key not in known:
                    errors.append(ConfigError(
                        path=f"{section}.{key}",
                        message=f"Unknown key '{key}' in '{section}'",
                        error_type="extra_key",
                    ))

    # Strategy-level extras
    strategies = raw.get("strategies")
    if isinstance(strategies, list):
        for i, strat in enumerate(strategies):
            if isinstance(strat, dict):
                for key in strat:
                    if key not in _KNOWN_STRATEGY_KEYS:
                        errors.append(ConfigError(
                            path=f"strategies[{i}].{key}",
                            message=f"Unknown key '{key}' in strategy #{i}",
                            error_type="extra_key",
                        ))

    return errors


def _pydantic_errors_to_config_errors(exc: ValidationError) -> List[ConfigError]:
    """Convert Pydantic ValidationError to our ConfigError list."""
    errors: List[ConfigError] = []
    for e in exc.errors():
        loc_parts = [str(p) for p in e.get("loc", [])]
        path = ".".join(loc_parts) if loc_parts else "<root>"

        err_type = e.get("type", "unknown")
        if "missing" in err_type:
            error_type = "missing"
        elif "type" in err_type:
            error_type = "type_error"
        else:
            error_type = "value_error"

        errors.append(ConfigError(
            path=path,
            message=e.get("msg", str(e)),
            error_type=error_type,
        ))
    return errors


def validate_config(
    raw: Dict[str, Any],
    *,
    strict: bool = True,
) -> ValidationResult:
    """
    Validate a raw config dict and return a structured result.

    Args:
        raw: Raw configuration dictionary (e.g. from YAML).
        strict: If True, unknown keys are errors.  If False, only
            schema violations are errors.

    Returns:
        ValidationResult with ok=True on success, or all errors.
    """
    all_errors: List[ConfigError] = []
    warnings: List[str] = []

    # 1) Extra-key detection (strict mode)
    if strict:
        all_errors.extend(find_extra_keys(raw))

    # 2) Pydantic schema validation
    from core.config.schema import ConfigSchema
    try:
        ConfigSchema(**raw)
    except ValidationError as exc:
        all_errors.extend(_pydantic_errors_to_config_errors(exc))
    except Exception as exc:
        all_errors.append(ConfigError(
            path="<root>",
            message=str(exc),
            error_type="value_error",
        ))

    return ValidationResult(
        ok=len(all_errors) == 0,
        errors=tuple(all_errors),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Config freeze (mutation guard)
# ---------------------------------------------------------------------------

def config_hash(raw: Dict[str, Any]) -> str:
    """
    Compute a deterministic hash of a config dict.

    Useful for detecting accidental mutation between load and use.
    """
    canonical = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class FrozenConfig:
    """
    Immutable wrapper around a validated config dict.

    Stores the hash at construction time; `check_integrity()` detects
    any post-load mutation.
    """
    _data: Dict[str, Any]
    _hash: str

    def __init__(self, raw: Dict[str, Any]) -> None:
        object.__setattr__(self, "_data", copy.deepcopy(raw))
        object.__setattr__(self, "_hash", config_hash(raw))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FrozenConfig is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenConfig is immutable")

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """
        Access a value by dotted path (e.g. 'risk.daily_loss_limit_usd').
        """
        keys = dotted_path.split(".")
        current: Any = self._data
        for k in keys:
            if isinstance(current, dict):
                current = current.get(k)
                if current is None:
                    return default
            else:
                return default
        return current

    @property
    def hash(self) -> str:
        return self._hash

    def check_integrity(self) -> bool:
        """Return True if internal data hasn't been mutated."""
        return config_hash(self._data) == self._hash

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the underlying data."""
        return copy.deepcopy(self._data)
