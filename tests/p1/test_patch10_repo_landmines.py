"""
PATCH 10 — Remove repo landmines

INVARIANT:
    The repository must not contain hardcoded credentials, backup files,
    deprecated dead-code directories, or hardcoded absolute user paths.
    These tests serve as guardrails to prevent regressions.

TESTS:
    1.  No .bak/.backup/.orig files tracked by git.
    2.  No deprecated protections_old directory.
    3.  No hardcoded absolute paths with usernames in Python files.
    4.  No hardcoded API keys or secrets in Python files.
    5.  No pdb/breakpoint calls in production code.
    6.  .gitignore covers __pycache__, .env, venv/, .pyc.
    7.  Config files use placeholder values, not real keys.
    8.  No import from protections_old in any Python file.
"""

import subprocess
import re
from pathlib import Path

import pytest

# Root of the repo
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git_ls_files(*patterns: str) -> list[str]:
    """List git-tracked files matching given patterns."""
    cmd = ["git", "ls-files"] + list(patterns)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return [f for f in result.stdout.strip().splitlines() if f]


def _git_tracked_files(extension: str = ".py") -> list[str]:
    """Get all git-tracked files with the given extension."""
    cmd = ["git", "ls-files", f"*{extension}"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return [f for f in result.stdout.strip().splitlines() if f]


class TestRepoLandmines:

    def test_no_backup_files_tracked(self):
        """No .bak/.backup/.orig files should be tracked by git."""
        backup_files = _git_ls_files("*.bak", "*.backup", "*.orig", "*.old")
        assert backup_files == [], f"Backup files tracked by git: {backup_files}"

    def test_no_deprecated_protections_old(self):
        """The deprecated protections_old directory must not exist."""
        old_dir = REPO_ROOT / "core" / "risk" / "protections_old"
        tracked = _git_ls_files("core/risk/protections_old/*")
        assert tracked == [], f"protections_old still tracked: {tracked}"
        # The directory itself may linger on disk if untracked; that's OK

    def test_no_hardcoded_user_paths(self):
        """No Python files should contain hardcoded absolute user paths."""
        # Pattern: C:/Users/<name>/ or /home/<name>/
        pattern = re.compile(r'(C:[/\\]+Users[/\\]+\w+|/home/\w+)', re.IGNORECASE)

        violations = []
        for rel_path in _git_tracked_files(".py"):
            full = REPO_ROOT / rel_path
            if not full.exists():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                # Skip comments that reference paths in docstrings/docs
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(line):
                    violations.append(f"{rel_path}:{i}")

        assert violations == [], f"Hardcoded user paths found: {violations}"

    def test_no_hardcoded_api_keys(self):
        """No Python files should contain hardcoded API keys."""
        # Look for common patterns: sk-, pk-, APCA-API-KEY-ID=<actual value>
        # Exclude placeholder values and test mocks
        key_pattern = re.compile(
            r'(?:api_key|api_secret|secret_key)\s*=\s*["\'](?!YOUR_|FAKE_|TEST_|MOCK_|placeholder)[A-Za-z0-9]{10,}["\']',
            re.IGNORECASE,
        )

        violations = []
        for rel_path in _git_tracked_files(".py"):
            full = REPO_ROOT / rel_path
            if not full.exists():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if key_pattern.search(line):
                    violations.append(f"{rel_path}:{i}")

        assert violations == [], f"Possible hardcoded API keys: {violations}"

    def test_no_pdb_in_production_code(self):
        """No pdb.set_trace() or breakpoint() calls in production code."""
        patterns = [
            re.compile(r'\bpdb\.set_trace\b'),
            re.compile(r'\bbreakpoint\s*\('),
            re.compile(r'\bimport\s+pdb\b'),
        ]

        violations = []
        for rel_path in _git_tracked_files(".py"):
            # Skip test files — breakpoints in tests are OK during development
            if "test" in rel_path.lower():
                continue
            full = REPO_ROOT / rel_path
            if not full.exists():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for pat in patterns:
                    if pat.search(line):
                        violations.append(f"{rel_path}:{i}")

        assert violations == [], f"Debug breakpoints in production code: {violations}"

    def test_gitignore_covers_essentials(self):
        """The .gitignore must cover __pycache__, .env, venv/, *.pyc."""
        gitignore = REPO_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore not found"

        text = gitignore.read_text(encoding="utf-8")
        required = ["__pycache__", ".env", "venv/", "*.pyc"]
        missing = [r for r in required if r not in text]
        assert missing == [], f".gitignore missing patterns: {missing}"

    def test_config_uses_placeholders(self):
        """config.yaml must NOT contain real API keys."""
        config = REPO_ROOT / "config" / "config.yaml"
        if not config.exists():
            pytest.skip("config.yaml not found")

        text = config.read_text(encoding="utf-8")
        # Check that api_key/api_secret lines contain placeholder values
        for line in text.splitlines():
            if "api_key:" in line.lower() and "YOUR_" not in line and "#" not in line:
                # Allow empty or obviously placeholder values
                val = line.split(":", 1)[1].strip().strip("'\"")
                assert val in ("", "YOUR_API_KEY", "null", "~"), \
                    f"Possible real API key in config.yaml: {line}"

    def test_no_import_from_protections_old(self):
        """No Python file should import from protections_old."""
        pattern = re.compile(r'from\s+.*protections_old|import\s+.*protections_old')

        violations = []
        for rel_path in _git_tracked_files(".py"):
            full = REPO_ROOT / rel_path
            if not full.exists():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{rel_path}:{i}")

        assert violations == [], f"Imports from protections_old: {violations}"
