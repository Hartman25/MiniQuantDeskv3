"""
PATCH 0: Repo hygiene tests.

Ensures:
- No Claude artifacts (.claude/, .claude-worktrees/) are tracked
- No Windows-problematic 'nul' device files are tracked

These checks prevent:
1. Local Claude state from polluting the repo
2. Windows Compress-Archive failures due to device file conflicts
"""

import subprocess
import pytest


def test_no_claude_artifacts_tracked():
    """PATCH 0: Ensure no .claude or .claude-worktrees paths are tracked."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )

    tracked_files = result.stdout.splitlines()

    # Check for any .claude or .claude-worktrees paths
    claude_artifacts = [
        f for f in tracked_files
        if f.startswith(".claude/") or f.startswith(".claude-worktrees/")
        or "/.claude/" in f or "/.claude-worktrees/" in f
    ]

    assert len(claude_artifacts) == 0, (
        f"Found tracked Claude artifacts (these should be in .gitignore):\n"
        f"{chr(10).join(claude_artifacts)}"
    )


def test_no_nul_files_tracked():
    """PATCH 0: Ensure no 'nul' device files are tracked (breaks Windows packaging)."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )

    tracked_files = result.stdout.splitlines()

    # Check for any files named 'nul' (Windows device file)
    nul_files = [
        f for f in tracked_files
        if f == "nul" or f.endswith("/nul")
    ]

    assert len(nul_files) == 0, (
        f"Found tracked 'nul' device files (these break Windows Compress-Archive):\n"
        f"{chr(10).join(nul_files)}"
    )
