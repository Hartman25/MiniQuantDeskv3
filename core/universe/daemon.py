"""
Universe Inbox Processor - Daemon Script

Runs Gate 2 processing in the background:
1. Reads candidates from inbox.jsonl
2. Applies filters
3. Writes decisions to decisions.jsonl
4. Updates universe_active.json

USAGE:
  python -m core.universe.daemon

ENV VARS:
  UNIVERSE_DATA_DIR - Path to universe data directory
  UNIVERSE_CHECK_INTERVAL - Seconds between checks (default: 60)
"""

from __future__ import annotations

import os
import sys
import time
import signal
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(repo_root))

from core.universe import UniverseInboxProcessor
from core.time import RealTimeClock
from core.logging import get_logger, LogStream


logger = get_logger(LogStream.SYSTEM)


class UniverseDaemon:
    """Background processor for universe inbox"""

    def __init__(self, data_dir: Path, check_interval: int = 60):
        self.data_dir = data_dir
        self.check_interval = check_interval
        self.running = True

        # Create processor
        clock = RealTimeClock()
        self.processor = UniverseInboxProcessor(data_dir, clock)

    def start(self):
        """Start processing loop"""
        logger.info(f"Starting universe daemon (interval={self.check_interval}s)")

        # Handle shutdown signals
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        while self.running:
            try:
                # Process new candidates
                decisions = self.processor.process_new_candidates(
                    has_open_position=False,  # TODO: Check broker state
                    has_open_orders=False,  # TODO: Check broker state
                )

                if decisions:
                    logger.info(f"Processed {len(decisions)} candidates:")
                    for d in decisions:
                        logger.info(f"  {d.symbol}: {d.decision} ({d.reason})")

                # Purge expired symbols
                self.processor.purge_expired_symbols()

                # Wait for next check
                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Error in daemon loop: {e}")
                time.sleep(5)  # Short sleep before retry

        logger.info("Universe daemon stopped")

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False


if __name__ == "__main__":
    # Get data directory from env or use default
    data_dir = os.getenv("UNIVERSE_DATA_DIR")
    if data_dir:
        data_dir = Path(data_dir)
    else:
        data_dir = Path(__file__).parent.parent.parent / "data" / "universe"

    # Get check interval
    check_interval = int(os.getenv("UNIVERSE_CHECK_INTERVAL", "60"))

    # Create and start daemon
    daemon = UniverseDaemon(data_dir, check_interval)
    daemon.start()
