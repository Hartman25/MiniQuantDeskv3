"""
Configuration loader with environment variable and secrets handling.

Loads configuration from:
1. config.yaml (main config)
2. .env.local (secrets file; loaded into process env)
3. Environment variables (highest priority)

Secrets are NEVER logged or displayed.
"""

import os
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv
import yaml


class ConfigLoader:
    """
    Loads and merges configuration from multiple sources.

    Priority (highest to lowest):
    1. OS Environment variables
    2. .env.local file
    3. config.yaml
    """

    # Secrets that must never be logged
    SECRET_KEYS = {
        "api_key",
        "api_secret",
        "broker_api_key",
        "broker_api_secret",
        "polygon_api_key",
        "alpha_vantage_api_key",
    }

    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.config_file = config_dir / "config.yaml"
        self.secrets_file = config_dir / ".env.local"

    def load(self) -> Dict[str, Any]:
        """
        Load configuration from all sources.

        Returns:
            Merged configuration dictionary

        Raises:
            FileNotFoundError: If config.yaml doesn't exist
            ValueError: If configuration is invalid
        """
        if not self.config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")

        # 1) Base config from YAML
        with open(self.config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config is None:
            raise ValueError(f"Empty configuration file: {self.config_file}")

        # 2) Load secrets from .env.local if present.
        #    Important: do NOT override already-set OS env vars.
        if self.secrets_file.exists():
            load_dotenv(self.secrets_file, override=False)

        # 3) Override broker credentials from environment (keep secrets out of YAML).
        # Supported names:
        #   - BROKER_API_KEY / BROKER_API_SECRET (preferred)
        #   - ALPACA_API_KEY / ALPACA_API_SECRET (legacy)
        #   - APCA_API_KEY_ID / APCA_API_SECRET_KEY (Alpaca SDK convention)
        if "broker" in config:
            env_key = (
                os.getenv("BROKER_API_KEY")
                or os.getenv("ALPACA_API_KEY")
                or os.getenv("APCA_API_KEY_ID")
            )
            env_secret = (
                os.getenv("BROKER_API_SECRET")
                or os.getenv("ALPACA_API_SECRET")
                or os.getenv("APCA_API_SECRET_KEY")
            )

            if env_key:
                config["broker"]["api_key"] = env_key
            if env_secret:
                config["broker"]["api_secret"] = env_secret

            # 4) Override other environment-specific settings
            paper_trading = os.getenv("PAPER_TRADING", "").strip().lower()
            if paper_trading in ("true", "1", "yes"):
                config["broker"]["paper_trading"] = True
            elif paper_trading in ("false", "0", "no"):
                config["broker"]["paper_trading"] = False

        return config

    def load_and_validate(self):
        """
        Load and validate configuration.

        Returns:
            ConfigSchema instance
        """
        from .schema import ConfigSchema

        config_dict = self.load()

        try:
            return ConfigSchema(**config_dict)
        except Exception as e:
            # Scrub secrets from error message
            error_msg = str(e)
            for secret in self.SECRET_KEYS:
                if secret in error_msg:
                    error_msg = error_msg.replace(secret, "[REDACTED]")
            raise ValueError(f"Configuration validation failed: {error_msg}") from e

    @staticmethod
    def scrub_secrets(config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace secret values with [REDACTED] for logging.

        Args:
            config_dict: Configuration dictionary

        Returns:
            Copy with secrets redacted
        """
        import copy

        scrubbed = copy.deepcopy(config_dict)

        def _scrub_recursive(d: Dict[str, Any]) -> None:
            for key, value in d.items():
                if key.lower() in ConfigLoader.SECRET_KEYS:
                    d[key] = "[REDACTED]"
                elif isinstance(value, dict):
                    _scrub_recursive(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            _scrub_recursive(item)

        _scrub_recursive(scrubbed)
        return scrubbed


def load_config(config_dir: Path = Path("config")):
    """
    Convenience function to load and validate configuration.

    Args:
        config_dir: Directory containing config files

    Returns:
        Validated ConfigSchema instance
    """
    loader = ConfigLoader(config_dir)
    return loader.load_and_validate()
