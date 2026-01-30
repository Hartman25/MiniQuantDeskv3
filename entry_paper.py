from __future__ import annotations

from pathlib import Path
from core.runtime.app import RunOptions, run

def run_paper(config_path: Path, run_interval_s: int = 60):
    return run(
        RunOptions(
            config_path=config_path,
            mode="paper",
            run_interval_s=run_interval_s
        )
    )

if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    cfg = base / "config" / "config_micro.yaml"   # or switch to config.yaml later
    raise SystemExit(run_paper(cfg, run_interval_s=60))
