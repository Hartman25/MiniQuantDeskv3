from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path

from core.runtime.app import RunOptions, run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="entry_paper",
        description="Run MiniQuantDesk in PAPER trading mode.",
    )
    p.add_argument(
        "--config",
        "-c",
        type=Path,
        default=(Path(__file__).resolve().parent / "config" / "config_micro.yaml"),
        help="Path to config YAML (default: ./config/config_micro.yaml)",
    )
    p.add_argument(
        "--interval",
        "-i",
        type=int,
        default=60,
        help="Loop interval in seconds (default: 60). Ignored when --once is set.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one cycle and exit (used for smoke tests).",
    )
    p.add_argument(
        "--env-check",
        action="store_true",
        help="Print whether API env vars were loaded (no secrets) and exit.",
    )
    return p.parse_args(argv)


def run_paper(*, config_path: Path, run_interval_s: int = 60, run_once: bool = False) -> int:
    return run(
        RunOptions(
            config_path=config_path,
            mode="paper",
            run_interval_s=run_interval_s,
            run_once=run_once,
        )
    )


def _load_env_local(cfg_path: Path) -> Path | None:
    """
    Loads KEY=VALUE lines into os.environ (if key not already set).
    Returns the Path that was loaded, or None.
    """
    env_candidates = [
        cfg_path.parent / ".env.local",  # next to chosen YAML (your case: ./config/.env.local)
        Path(__file__).resolve().parent / "config" / ".env.local",
        Path(__file__).resolve().parent / ".env.local",
    ]

    for env_path in env_candidates:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            return env_path
    return None


def _apply_env_aliases() -> None:
    # Compatibility aliases: allow BROKER_* but also expose ALPACA_* names
    if os.getenv("BROKER_API_KEY") and not os.getenv("ALPACA_API_KEY"):
        os.environ["ALPACA_API_KEY"] = os.getenv("BROKER_API_KEY", "")
    if os.getenv("BROKER_API_SECRET") and not os.getenv("ALPACA_API_SECRET"):
        os.environ["ALPACA_API_SECRET"] = os.getenv("BROKER_API_SECRET", "")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    cfg_path: Path = args.config.expanduser().resolve()
    if not cfg_path.exists():
        print(f"[entry_paper] ERROR: config file not found: {cfg_path}", file=sys.stderr)
        return 2
    if cfg_path.is_dir():
        print(
            f"[entry_paper] ERROR: config path is a directory (expected a YAML file): {cfg_path}",
            file=sys.stderr,
        )
        return 2

    loaded_env = _load_env_local(cfg_path)
    _apply_env_aliases()

    if args.env_check:
        print(
            "[env-check]",
            f"loaded_env_file={str(loaded_env) if loaded_env else 'NONE'}",
            f"BROKER_API_KEY={bool(os.getenv('BROKER_API_KEY'))}",
            f"BROKER_API_SECRET={bool(os.getenv('BROKER_API_SECRET'))}",
            f"ALPACA_API_KEY={bool(os.getenv('ALPACA_API_KEY'))}",
            f"ALPACA_API_SECRET={bool(os.getenv('ALPACA_API_SECRET'))}",
        )
        return 0

    interval = 0 if args.once else max(1, int(args.interval))

    return run_paper(
        config_path=cfg_path,
        run_interval_s=interval,
        run_once=bool(args.once),
    )


if __name__ == "__main__":
    raise SystemExit(main())
