"""
P2-B4 — Known Failure Regimes Documented

INVARIANT:
    The file docs/STRATEGY_FAILURE_MODES.md MUST exist and document
    known failure regimes for VWAPMicroMeanReversion.

TESTS:
    1. File exists
    2. File is non-empty (>200 chars)
    3-7. Required sections present
"""

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "STRATEGY_FAILURE_MODES.md"


class TestFailureRegimesDoc:

    def test_file_exists(self):
        assert DOC_PATH.exists(), f"{DOC_PATH} must exist"

    def test_file_not_empty(self):
        content = DOC_PATH.read_text(encoding="utf-8")
        assert len(content) > 200, "Doc must be substantial (>200 chars)"

    @pytest.mark.parametrize("section", [
        "Gap",
        "Low Liquidity",
        "VWAP",
        "Volatility",
        "Trending",
    ])
    def test_covers_required_regime(self, section):
        content = DOC_PATH.read_text(encoding="utf-8").lower()
        assert section.lower() in content, (
            f"Doc must mention '{section}' failure regime"
        )
