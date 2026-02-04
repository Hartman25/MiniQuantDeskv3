"""
P1-N1 — Execution Invariant Documentation

Verify that the in-repo execution invariant documentation exists and
covers the core guarantees.  This test is a guard against documentation
rot: if someone deletes or empties the doc, CI catches it.
"""

import pytest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/p1/phase1_nice -> repo root
INVARIANTS_DOC = REPO_ROOT / "docs" / "EXECUTION_INVARIANTS.md"


class TestExecutionInvariantDocExists:

    def test_invariants_file_exists(self):
        assert INVARIANTS_DOC.exists(), (
            f"docs/EXECUTION_INVARIANTS.md must exist at {INVARIANTS_DOC}"
        )

    def test_invariants_file_not_empty(self):
        if not INVARIANTS_DOC.exists():
            pytest.skip("File does not exist yet")
        content = INVARIANTS_DOC.read_text(encoding="utf-8")
        assert len(content.strip()) > 200, (
            "EXECUTION_INVARIANTS.md must have substantive content (>200 chars)"
        )

    @pytest.mark.parametrize("section", [
        "Order State Machine",
        "Single Position",
        "Duplicate Order",
        "Circuit Breaker",
        "Recovery",
        "Protective Stop",
    ])
    def test_invariants_covers_required_section(self, section):
        """Each core invariant area must be mentioned."""
        if not INVARIANTS_DOC.exists():
            pytest.skip("File does not exist yet")
        content = INVARIANTS_DOC.read_text(encoding="utf-8").lower()
        assert section.lower() in content, (
            f"EXECUTION_INVARIANTS.md must mention '{section}'"
        )
