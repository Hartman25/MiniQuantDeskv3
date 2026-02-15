"""
PATCH 11 tests: Retry timeout enforcement.

Problem: Retries can exceed MAX_RETRIES * RETRY_DELAY without hard timeout,
causing indefinite hangs on persistent failures.

Solution: Add absolute timeout enforcement in retry logic.

Tests:
1. Retry loop respects absolute timeout
2. Timeout logged when exceeded
3. Normal retries work within timeout
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import time

import pytest


def test_retry_timeout_enforced():
    """PATCH 11: Retries stop when absolute timeout is exceeded."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector, BrokerConnectionError
    import logging

    # Create minimal connector stub
    obj = object.__new__(AlpacaBrokerConnector)
    obj.logger = logging.getLogger("test_retry_timeout")
    obj.MAX_RETRIES = 10  # High retry count
    obj.RETRY_DELAY_SECONDS = 0.1
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj.RETRY_TIMEOUT_SECONDS = 0.5  # Short timeout for testing

    # Mock client that always fails with retryable error
    mock_client = MagicMock()
    obj.client = mock_client
    mock_client.get_account.side_effect = ConnectionError("Persistent failure")

    start_time = time.time()

    # Should stop retrying after 0.5s timeout, not after 10 retries * 0.1s = 1s
    # get_account_info wraps TimeoutError in BrokerConnectionError
    with pytest.raises(BrokerConnectionError, match="Retry timeout exceeded"):
        obj.get_account_info()

    elapsed = time.time() - start_time

    # Should have stopped at ~0.5s timeout, not 1s (10 retries)
    assert elapsed < 0.8, f"Timeout enforcement failed: took {elapsed}s, expected <0.8s"


def test_timeout_logged_when_exceeded():
    """PATCH 11: Timeout exceeded events are logged."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector, BrokerConnectionError
    import logging
    from logging.handlers import MemoryHandler

    obj = object.__new__(AlpacaBrokerConnector)

    # Capture log messages
    log_handler = MemoryHandler(capacity=100)
    logger = logging.getLogger("test_retry_timeout_logging")
    logger.addHandler(log_handler)
    logger.setLevel(logging.WARNING)
    obj.logger = logger

    obj.MAX_RETRIES = 10
    obj.RETRY_DELAY_SECONDS = 0.1
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj.RETRY_TIMEOUT_SECONDS = 0.3

    mock_client = MagicMock()
    obj.client = mock_client
    mock_client.get_account.side_effect = ConnectionError("Always fails")

    with pytest.raises(BrokerConnectionError):  # Wrapped TimeoutError
        obj.get_account_info()

    # Check logs for timeout warning
    log_handler.flush()
    records = [r for r in log_handler.buffer if r.levelno >= logging.WARNING]

    # Should have logged timeout exceeded
    timeout_logs = [r for r in records if "timeout" in r.getMessage().lower()]
    assert len(timeout_logs) > 0, "Expected timeout warning in logs"


def test_normal_retries_work_within_timeout():
    """PATCH 11: Normal retries still work when within timeout."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector
    import logging

    obj = object.__new__(AlpacaBrokerConnector)
    obj.logger = logging.getLogger("test_retry_success")
    obj.MAX_RETRIES = 5
    obj.RETRY_DELAY_SECONDS = 0.05
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj.RETRY_TIMEOUT_SECONDS = 10.0  # Long timeout

    mock_client = MagicMock()
    obj.client = mock_client

    # Fail twice, then succeed
    call_count = 0
    def side_effect():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Transient failure")
        return MagicMock(buying_power="100000", cash="50000", portfolio_value="150000")

    mock_client.get_account.side_effect = side_effect

    # Should succeed on 3rd attempt
    result = obj.get_account_info()
    assert result["buying_power"] == Decimal("100000")
    assert call_count == 3
