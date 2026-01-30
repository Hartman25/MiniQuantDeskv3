from __future__ import annotations

from pathlib import Path

from core.runtime.app import RunOptions, run_app


def run_live(config_path: Path, run_once: bool = False) -> int:
    return run_app(RunOptions(config_path=config_path, mode="live", run_once=run_once))
