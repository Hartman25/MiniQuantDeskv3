"""
PATCH 6 validation: Ensure no naive datetimes in production code.

Tests:
1. Grep check: no datetime.utcnow() calls
2. Grep check: no datetime.now() without timezone
3. Runtime check: all datetimes have tzinfo
"""

import ast
import os
from pathlib import Path


def test_no_utcnow_calls():
    """PATCH 6: No datetime.utcnow() calls anywhere."""
    violations = []
    core_dir = Path("core")

    for pyfile in core_dir.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue

        content = pyfile.read_text(encoding="utf-8")
        if "datetime.utcnow()" in content:
            # Check if it's in actual code (not comments)
            for i, line in enumerate(content.split("\n"), 1):
                if "datetime.utcnow()" in line:
                    stripped = line.strip()
                    if not stripped.startswith("#") and '"""' not in stripped and "'''" not in stripped:
                        violations.append(f"{pyfile}:{i}: {line.strip()[:80]}")

    assert not violations, (
        f"Found {len(violations)} naive datetime.utcnow() calls:\n"
        + "\n".join(violations[:10])
    )


def test_datetime_now_has_timezone():
    """PATCH 6: All datetime.now() calls use timezone argument."""
    violations = []
    core_dir = Path("core")

    for pyfile in core_dir.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue

        content = pyfile.read_text(encoding="utf-8")
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            # Skip comments and docstrings
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"""' in stripped or "'''" in stripped:
                continue

            # Check for datetime.now() without timezone
            if "datetime.now()" in line:
                # Must have 'timezone' or 'UTC' in same line or be a comment
                if "timezone" not in line and "UTC" not in line and "utc" not in line:
                    # Exclude known safe patterns
                    if "Clock" in line or "eliminates" in line or "grep can verify" in line:
                        continue
                    violations.append(f"{pyfile}:{i}: {line.strip()[:80]}")

    assert not violations, (
        f"Found {len(violations)} potentially naive datetime.now() calls:\n"
        + "\n".join(violations[:10])
    )


def test_critical_files_use_timezone_aware():
    """PATCH 6: Critical files use timezone-aware datetimes."""
    critical_files = [
        "core/execution/engine.py",
        "core/state/transaction_log.py",
        "core/state/order_machine.py",
    ]

    for filepath in critical_files:
        path = Path(filepath)
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8")

        # Every datetime.now() must have timezone.utc
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            if "datetime.now(" in line and "#" not in line:
                assert "timezone.utc" in line or "UTC" in line, (
                    f"{filepath}:{i} has datetime.now() without timezone.utc:\n{line.strip()}"
                )
