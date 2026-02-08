from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv


def load_env(
    filenames: Iterable[str] = (".env.local", ".env"),
    search_dirs: Optional[list[Path]] = None,
    override: bool = False,
) -> list[Path]:
    """
    Load env files from project root AND config/ directory.

    Returns list of env files actually loaded.
    """
    if search_dirs is None:
        project_root = Path(__file__).resolve().parents[2]
        search_dirs = [
            project_root,
            project_root / "config",
        ]

    loaded: list[Path] = []
    for d in search_dirs:
        for name in filenames:
            p = d / name
            if p.exists() and p.is_file():
                load_dotenv(dotenv_path=p, override=override)
                loaded.append(p)

    return loaded


def env_flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}
