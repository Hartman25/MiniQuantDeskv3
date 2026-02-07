from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.runtime.app import RunOptions, run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="entry_live",
        description="Run MiniQuantDesk in LIVE trading mode. Use with extreme caution.",
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
        help="Run exactly one cycle and exit (used for live connectivity smoke tests).",
    )
    p.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="Required for LIVE mode. Prevents accidental execution.",
    )
    return p.parse_args(argv)


def run_live(*, config_path: Path, run_interval_s: int = 60, run_once: bool = False) -> int:
    return run(
        RunOptions(
            config_path=config_path,
            mode="live",
            run_interval_s=run_interval_s,
            run_once=run_once,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if not args.i_know_what_im_doing:
        print("[entry_live] Refusing to run LIVE without --i-know-what-im-doing", file=sys.stderr)
        return 2

    cfg_path: Path = args.config.expanduser().resolve()
    if not cfg_path.exists():
        print(f"[entry_live] ERROR: config file not found: {cfg_path}", file=sys.stderr)
        return 2
    if cfg_path.is_dir():
        print(
            f"[entry_live] ERROR: config path is a directory (expected a YAML file): {cfg_path}",
            file=sys.stderr,
        )
        return 2

    interval = 0 if args.once else max(1, int(args.interval))
    return run_live(config_path=cfg_path, run_interval_s=interval, run_once=bool(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
