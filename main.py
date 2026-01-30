#!/usr/bin/env python3
"""
MiniQuantDesk v2 - Main Entrypoint

USAGE:
    python main.py paper --config config/config.yaml
    python main.py live --config config/config.yaml
    python main.py backtest --config config/config.yaml --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.runtime.app import RunOptions, run_app


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="MiniQuantDesk v2 - Algorithmic Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='mode', help='Trading mode')
    subparsers.required = True
    
    # Paper trading mode
    paper_parser = subparsers.add_parser('paper', help='Run in paper trading mode')
    paper_parser.add_argument(
        '--config',
        type=str,
        default='config/config.yaml',
        help='Path to config file (default: config/config.yaml)'
    )
    paper_parser.add_argument(
        '--run-once',
        action='store_true',
        help='Run one cycle and exit (for testing)'
    )
    
    # Live trading mode
    live_parser = subparsers.add_parser('live', help='Run in LIVE trading mode')
    live_parser.add_argument(
        '--config',
        type=str,
        default='config/config.yaml',
        help='Path to config file (default: config/config.yaml)'
    )
    live_parser.add_argument(
        '--run-once',
        action='store_true',
        help='Run one cycle and exit (for testing)'
    )
    
    # Backtest mode
    backtest_parser = subparsers.add_parser('backtest', help='Run backtest')
    backtest_parser.add_argument(
        '--config',
        type=str,
        default='config/config.yaml',
        help='Path to config file'
    )
    backtest_parser.add_argument(
        '--start',
        type=str,
        required=True,
        help='Start date (YYYY-MM-DD)'
    )
    backtest_parser.add_argument(
        '--end',
        type=str,
        required=True,
        help='End date (YYYY-MM-DD)'
    )
    backtest_parser.add_argument(
        '--output',
        type=str,
        default='backtest_results.json',
        help='Output file for results'
    )
    
    args = parser.parse_args()
    
    # Validate config file exists
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        print(f"Create config file or specify --config path")
        sys.exit(1)
    
    # Route to appropriate handler
    if args.mode in ('paper', 'live'):
        # Runtime trading
        opts = RunOptions(
            config_path=config_path,
            mode=args.mode,
            run_once=args.run_once
        )
        
        print(f"Starting MiniQuantDesk in {args.mode.upper()} mode...")
        print(f"Config: {config_path}")
        print(f"Press Ctrl+C to stop")
        print("-" * 60)
        
        exit_code = run_app(opts)
        sys.exit(exit_code)
        
    elif args.mode == 'backtest':
        # Backtest
        print(f"ERROR: Backtest mode not yet implemented in main.py")
        print(f"Use: python -m backtest.engine (see backtest/ directory)")
        sys.exit(1)


if __name__ == '__main__':
    main()
