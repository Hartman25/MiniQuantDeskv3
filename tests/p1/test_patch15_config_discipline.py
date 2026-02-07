"""
PATCH 15 — Enforce config discipline with schema validation

INVARIANT:
    ``validate_config()`` returns structured errors for any raw dict.
    Unknown keys are rejected in strict mode.  ``FrozenConfig`` prevents
    post-load mutation.  ``config_hash()`` detects drift.

TESTS:
    1.  Valid config → ok=True, zero errors.
    2.  Missing required section → error with path.
    3.  Invalid type → type_error.
    4.  Value out of range → value_error.
    5.  Extra top-level key (strict) → extra_key error.
    6.  Extra section key (strict) → extra_key error.
    7.  Extra strategy key → extra_key error.
    8.  Non-strict ignores extra keys.
    9.  Multiple errors collected at once.
   10.  ValidationResult.summary() is human-readable.
   11.  ConfigError.__str__ format.
   12.  config_hash deterministic for same data.
   13.  config_hash differs for different data.
   14.  FrozenConfig prevents setattr.
   15.  FrozenConfig.get() with dotted path.
   16.  FrozenConfig.check_integrity() passes after construction.
"""

from decimal import Decimal

import pytest

from core.config.validator import (
    ConfigError,
    FrozenConfig,
    ValidationResult,
    config_hash,
    find_extra_keys,
    validate_config,
)


def _valid_raw() -> dict:
    """Return a minimal valid config dict."""
    return {
        "risk": {
            "initial_account_value": "1000",
            "max_open_positions": 1,
            "max_position_size_pct": "10",
            "daily_loss_limit_usd": "100",
            "weekly_loss_limit_usd": "300",
            "risk_per_trade_pct": "1",
            "circuit_breaker_enabled": True,
            "circuit_breaker_loss_pct": "5",
            "halt_duration_minutes": 30,
        },
        "broker": {
            "name": "alpaca",
            "api_key": "ABCDEFGHIJKLMNOP",
            "api_secret": "ABCDEFGHIJKLMNOP",
            "base_url": "https://paper-api.alpaca.markets",
            "paper_trading": True,
        },
        "data": {
            "primary_provider": "alpaca",
            "max_staleness_seconds": 90,
            "cache_enabled": True,
        },
        "strategies": [
            {
                "name": "TestStrat",
                "symbols": ["SPY"],
                "timeframe": "1Min",
            }
        ],
        "session": {
            "cycle_interval_seconds": 60,
            "max_daily_trades": 3,
        },
    }


class TestValidateConfig:

    def test_valid_config_ok(self):
        """Valid config → ok=True."""
        result = validate_config(_valid_raw())
        assert result.ok is True
        assert result.error_count == 0

    def test_missing_required_section(self):
        """Missing broker section → error."""
        raw = _valid_raw()
        del raw["broker"]
        result = validate_config(raw, strict=False)
        assert result.ok is False
        assert any(e.error_type == "missing" for e in result.errors)
        assert any("broker" in e.path for e in result.errors)

    def test_invalid_type(self):
        """String where int expected → error on that field."""
        raw = _valid_raw()
        raw["session"]["cycle_interval_seconds"] = "not-a-number"
        result = validate_config(raw, strict=False)
        assert result.ok is False
        assert any("cycle_interval_seconds" in e.path for e in result.errors)

    def test_value_out_of_range(self):
        """Value below minimum → value_error."""
        raw = _valid_raw()
        raw["session"]["cycle_interval_seconds"] = 5  # min is 30
        result = validate_config(raw, strict=False)
        assert result.ok is False
        assert any(e.error_type == "value_error" for e in result.errors)

    def test_extra_top_level_key_strict(self):
        """Unknown top-level key in strict mode → extra_key."""
        raw = _valid_raw()
        raw["mystery_section"] = {"foo": 1}
        result = validate_config(raw, strict=True)
        assert result.ok is False
        assert any(
            e.error_type == "extra_key" and e.path == "mystery_section"
            for e in result.errors
        )

    def test_extra_section_key_strict(self):
        """Unknown key inside a section → extra_key."""
        raw = _valid_raw()
        raw["risk"]["unknown_limit"] = 999
        result = validate_config(raw, strict=True)
        assert result.ok is False
        assert any(
            e.error_type == "extra_key" and "risk.unknown_limit" in e.path
            for e in result.errors
        )

    def test_extra_strategy_key(self):
        """Unknown key in strategy → extra_key."""
        raw = _valid_raw()
        raw["strategies"][0]["bonus_field"] = True
        result = validate_config(raw, strict=True)
        assert result.ok is False
        assert any(
            e.error_type == "extra_key" and "strategies[0].bonus_field" in e.path
            for e in result.errors
        )

    def test_non_strict_ignores_extras(self):
        """Non-strict mode ignores extra keys."""
        raw = _valid_raw()
        raw["mystery_section"] = {"foo": 1}
        raw["risk"]["unknown_limit"] = 999
        result = validate_config(raw, strict=False)
        assert result.ok is True

    def test_multiple_errors_collected(self):
        """Multiple issues produce multiple errors at once."""
        raw = _valid_raw()
        del raw["broker"]
        raw["session"]["cycle_interval_seconds"] = 5  # out of range
        raw["mystery_top"] = {}
        result = validate_config(raw, strict=True)
        assert result.ok is False
        assert result.error_count >= 2

    def test_summary_human_readable(self):
        """summary() returns a formatted string."""
        raw = _valid_raw()
        result = validate_config(raw)
        assert "OK" in result.summary()

        raw2 = _valid_raw()
        del raw2["broker"]
        result2 = validate_config(raw2, strict=False)
        assert "INVALID" in result2.summary()
        assert "errors" in result2.summary()

    def test_config_error_str(self):
        """ConfigError.__str__ has expected format."""
        e = ConfigError(
            path="risk.daily_loss_limit_usd",
            message="must be >= 1",
            error_type="value_error",
        )
        s = str(e)
        assert "[value_error]" in s
        assert "risk.daily_loss_limit_usd" in s

    def test_config_hash_deterministic(self):
        """Same data → same hash."""
        d1 = _valid_raw()
        d2 = _valid_raw()
        assert config_hash(d1) == config_hash(d2)

    def test_config_hash_differs(self):
        """Different data → different hash."""
        d1 = _valid_raw()
        d2 = _valid_raw()
        d2["risk"]["daily_loss_limit_usd"] = "200"
        assert config_hash(d1) != config_hash(d2)

    def test_frozen_config_immutable(self):
        """FrozenConfig blocks setattr."""
        fc = FrozenConfig(_valid_raw())
        with pytest.raises(AttributeError, match="immutable"):
            fc.x = 1

    def test_frozen_config_get(self):
        """FrozenConfig.get() navigates dotted paths."""
        fc = FrozenConfig(_valid_raw())
        assert fc.get("risk.daily_loss_limit_usd") == "100"
        assert fc.get("broker.api_key") == "ABCDEFGHIJKLMNOP"
        assert fc.get("nonexistent.path") is None
        assert fc.get("nonexistent.path", "default") == "default"

    def test_frozen_config_integrity(self):
        """Integrity check passes on fresh FrozenConfig."""
        fc = FrozenConfig(_valid_raw())
        assert fc.check_integrity() is True
