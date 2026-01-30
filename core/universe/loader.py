"""
Universe Loader - Trading Bot Integration

Loads universe symbols for the trading bot with multiple modes:

MODES:
- scanner: Use only scanner-generated symbols (from universe_active.json)
- accepted: Use CORE + accepted symbols from Gate 2
- hybrid: (DEFAULT) Use CORE + accepted (recommended)

USAGE IN TRADING BOT:
```python
from core.universe import UniverseLoader

# In app.py or strategy initialization:
universe_loader = UniverseLoader(mode="hybrid")
symbols = universe_loader.get_symbols()

# symbols = ["SPY", "QQQ", "TSLA", "NVDA", ...]  # CORE + accepted
```

ENV VARS:
  UNIVERSE_MODE=hybrid|scanner|accepted (default: hybrid)
  UNIVERSE_DATA_DIR=path/to/data/universe (default: data/universe)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from core.logging import get_logger, LogStream


logger = get_logger(LogStream.SYSTEM)


# Core universe (always included in hybrid/accepted modes)
CORE_SYMBOLS = ["SPY", "QQQ"]


class UniverseLoader:
    """Loads universe symbols for trading bot"""

    def __init__(
        self,
        mode: Optional[str] = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            mode: "scanner", "accepted", or "hybrid" (default: hybrid)
            data_dir: Path to data/universe directory
        """
        self.mode = mode or os.getenv("UNIVERSE_MODE", "hybrid").lower()

        if self.mode not in ["scanner", "accepted", "hybrid"]:
            logger.warning(f"Invalid universe mode '{self.mode}', using 'hybrid'")
            self.mode = "hybrid"

        if data_dir is None:
            repo_root = Path(__file__).parent.parent.parent
            data_dir = repo_root / "data" / "universe"

        self.data_dir = data_dir
        self.universe_path = data_dir / "universe_active.json"

    def get_symbols(self) -> List[str]:
        """
        Load symbols based on mode.
        
        Returns:
            List of symbols to trade
        """
        if self.mode == "scanner":
            return self._load_scanner_only()
        elif self.mode == "accepted":
            return self._load_accepted_only()
        else:  # hybrid (default)
            return self._load_hybrid()

    def _load_scanner_only(self) -> List[str]:
        """Load only accepted symbols from universe_active.json"""
        try:
            with open(self.universe_path, "r") as f:
                data = json.load(f)
            
            accepted = data.get("accepted", [])
            logger.info(f"Universe (scanner mode): {len(accepted)} symbols")
            return accepted

        except Exception as e:
            logger.error(f"Failed to load universe_active.json: {e}")
            return []

    def _load_accepted_only(self) -> List[str]:
        """Load CORE + accepted symbols"""
        try:
            with open(self.universe_path, "r") as f:
                data = json.load(f)
            
            core = data.get("core", CORE_SYMBOLS)
            accepted = data.get("accepted", [])
            
            # Combine and deduplicate
            symbols = list(set(core + accepted))
            logger.info(f"Universe (accepted mode): core={len(core)} accepted={len(accepted)} total={len(symbols)}")
            return sorted(symbols)

        except Exception as e:
            logger.error(f"Failed to load universe_active.json: {e}, using CORE only")
            return CORE_SYMBOLS.copy()

    def _load_hybrid(self) -> List[str]:
        """Load CORE + accepted symbols (same as accepted mode)"""
        # Hybrid is the same as accepted mode (CORE + accepted)
        return self._load_accepted_only()

    def refresh(self) -> List[str]:
        """Force reload symbols from disk"""
        return self.get_symbols()

    def get_mode(self) -> str:
        """Get current mode"""
        return self.mode


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def get_universe_symbols(mode: Optional[str] = None) -> List[str]:
    """
    Get universe symbols (convenience function).
    
    Args:
        mode: "scanner", "accepted", or "hybrid" (default from env)
        
    Returns:
        List of symbols to trade
    """
    loader = UniverseLoader(mode=mode)
    return loader.get_symbols()
