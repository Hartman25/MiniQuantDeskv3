from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.runtime.app import RunOptions, run

# Optional: only imported for cleaner error output, without breaking if refactors happen.
try:
    from core.brokers.alpaca_connector import BrokerConnectionError
except Exception:  # pragma: no cover
    BrokerConnectionError = Exception  # type: ignore


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
        "--env-check",
        action="store_true",
        help="Print whether API env vars were loaded (no secrets) and exit.",
    )
    p.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="Required for LIVE mode. Prevents accidental execution.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Bypass safety guards (NOT recommended).",
    )
    return p.parse_args(argv)


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


def _looks_like_paper_key(api_key: str) -> bool:
    """
    Alpaca commonly uses:
      - Paper keys: start with 'PK'
      - Live keys:  start with 'AK'
    This is a heuristic safety guard.
    """
    k = (api_key or "").strip()
    return k.upper().startswith("PK")


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

    if not args.i_know_what_im_doing and not args.force:
        print("[entry_live] Refusing to run LIVE without --i-know-what-im-doing", file=sys.stderr)
        return 2

    # Smoke safety: prevent “paper keys against live endpoint” => 401 + scary traceback.
    # If you only have paper keys configured, use entry_paper.py for smoke until you create live keys.
    api_key = os.getenv("ALPACA_API_KEY", "") or os.getenv("BROKER_API_KEY", "")
    if api_key and _looks_like_paper_key(api_key) and not args.force:
        print(
            "[entry_live] Detected PAPER API key (looks like 'PK...'). Refusing LIVE run.\n"
            "Create LIVE Alpaca keys (commonly 'AK...') or run PAPER smoke with entry_paper.py.\n"
            "Override with --force (not recommended).",
            file=sys.stderr,
        )
        return 2

    interval = 0 if args.once else int(args.interval)

    # Config-driven fallback: --interval 0 (without --once) reads from YAML.
    if interval == 0 and not args.once:
        from core.config.schema import ConfigSchema
        try:
            import yaml
            with open(cfg_path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            _cfg = ConfigSchema(**raw)
            interval = _cfg.session.cycle_interval_seconds
        except Exception:
            interval = 60  # hard default
    if not args.once:
        interval = max(1, interval)

    # Live smoke: never place orders when running a one-cycle connectivity check.
    if args.once and not args.force:
        os.environ["MQD_SMOKE_NO_ORDERS"] = "1"
        print("[entry_live] Smoke mode: order placement is DISABLED for this --once run.")

    try:
        return run_live(config_path=cfg_path, run_interval_s=interval, run_once=bool(args.once))
    except KeyboardInterrupt:
        return 0
    except BrokerConnectionError as e:
        print(f"[entry_live] BROKER CONNECTION FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # Keep a clean failure for smoke runs; real debugging stays in logs/tracebacks if needed.
        print(f"[entry_live] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
