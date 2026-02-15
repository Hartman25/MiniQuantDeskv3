"""
PATCH 8 tests: Market clock cache refresh on state transitions.

Problem: Clock cache with 15s TTL can miss market open/close transitions.
Example: Cache "is_open=False" at 9:28 AM, remains stale until 9:30+ AM.

Solution: Invalidate cache when current time crosses a known boundary.

Tests:
1. Cache doesn't span market open boundary
2. Cache doesn't span market close boundary
3. Normal caching still works within session
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import time


def test_cache_invalidated_across_market_open():
    """PATCH 8: Clock cache is invalidated when crossing market open."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector
    import logging

    # Create minimal connector stub
    obj = object.__new__(AlpacaBrokerConnector)
    obj.logger = logging.getLogger("test_clock_cache")
    obj.MAX_RETRIES = 1
    obj.RETRY_DELAY_SECONDS = 0.01
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj._clock_cache = None
    obj._clock_cache_ts = 0.0
    obj._clock_cache_ttl = 300.0  # 5 minute TTL (long enough to test boundary invalidation)

    # Mock Alpaca client
    mock_client = MagicMock()
    obj.client = mock_client

    # Use current time as base, set next_open 1 second in the future
    base_time = datetime.now(timezone.utc)
    market_open_time = base_time + timedelta(seconds=1)

    # First call: market closed
    clock_obj_1 = MagicMock()
    clock_obj_1.is_open = False
    clock_obj_1.timestamp = base_time
    clock_obj_1.next_open = market_open_time
    clock_obj_1.next_close = base_time + timedelta(hours=7)
    mock_client.get_clock.return_value = clock_obj_1

    result1 = obj.get_clock()
    assert result1["is_open"] is False
    assert mock_client.get_clock.call_count == 1

    # Second call: still before open, cache should be used
    result2 = obj.get_clock()
    assert result2["is_open"] is False
    assert mock_client.get_clock.call_count == 1  # Cache hit

    # Now mock clock returns market open
    clock_obj_2 = MagicMock()
    clock_obj_2.is_open = True
    clock_obj_2.timestamp = market_open_time
    clock_obj_2.next_open = market_open_time + timedelta(days=1)
    clock_obj_2.next_close = market_open_time + timedelta(hours=6)
    mock_client.get_clock.return_value = clock_obj_2

    # PATCH 8: Wait for time to cross next_open boundary
    time.sleep(1.1)  # Wait past market_open_time
    result3 = obj.get_clock()

    # BEFORE PATCH 8: Would still return False from cache (wrong!)
    # AFTER PATCH 8: Should fetch fresh data and return True
    assert result3["is_open"] is True, "Cache should be invalidated when crossing market open"
    assert mock_client.get_clock.call_count == 2, "Should have made a fresh API call"


def test_cache_invalidated_across_market_close():
    """PATCH 8: Clock cache is invalidated when crossing market close."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector
    import logging

    obj = object.__new__(AlpacaBrokerConnector)
    obj.logger = logging.getLogger("test_clock_cache")
    obj.MAX_RETRIES = 1
    obj.RETRY_DELAY_SECONDS = 0.01
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj._clock_cache = None
    obj._clock_cache_ts = 0.0
    obj._clock_cache_ttl = 300.0

    mock_client = MagicMock()
    obj.client = mock_client

    # Set next_close 1 second in the future
    base_time = datetime.now(timezone.utc)
    market_close_time = base_time + timedelta(seconds=1)

    # First call: market open
    clock_obj_1 = MagicMock()
    clock_obj_1.is_open = True
    clock_obj_1.timestamp = base_time
    clock_obj_1.next_open = base_time + timedelta(days=1)
    clock_obj_1.next_close = market_close_time
    mock_client.get_clock.return_value = clock_obj_1

    result1 = obj.get_clock()
    assert result1["is_open"] is True
    assert mock_client.get_clock.call_count == 1

    # Now mock clock returns market closed
    clock_obj_2 = MagicMock()
    clock_obj_2.is_open = False
    clock_obj_2.timestamp = market_close_time + timedelta(seconds=1)
    clock_obj_2.next_open = base_time + timedelta(days=1)
    clock_obj_2.next_close = base_time + timedelta(days=1, hours=6)
    mock_client.get_clock.return_value = clock_obj_2

    time.sleep(1.1)  # Wait past market_close_time
    result2 = obj.get_clock()

    # PATCH 8: Should invalidate cache when crossing next_close
    assert result2["is_open"] is False
    assert mock_client.get_clock.call_count == 2


def test_cache_works_within_session():
    """PATCH 8: Normal caching still works when not crossing boundaries."""
    from core.brokers.alpaca_connector import AlpacaBrokerConnector
    import logging

    obj = object.__new__(AlpacaBrokerConnector)
    obj.logger = logging.getLogger("test_clock_cache")
    obj.MAX_RETRIES = 1
    obj.RETRY_DELAY_SECONDS = 0.01
    obj.RETRY_BACKOFF_MULTIPLIER = 1.0
    obj._clock_cache = None
    obj._clock_cache_ts = 0.0
    obj._clock_cache_ttl = 10.0  # 10 second TTL

    mock_client = MagicMock()
    obj.client = mock_client

    # Set boundaries far in the future (not crossing during test)
    base_time = datetime.now(timezone.utc)

    clock_obj = MagicMock()
    clock_obj.is_open = True
    clock_obj.timestamp = base_time
    clock_obj.next_open = base_time + timedelta(days=1)  # Tomorrow
    clock_obj.next_close = base_time + timedelta(hours=5)  # 5 hours away
    mock_client.get_clock.return_value = clock_obj

    result1 = obj.get_clock()
    assert result1["is_open"] is True
    assert mock_client.get_clock.call_count == 1

    # Multiple calls within TTL and not near boundary => cache hit
    for _ in range(5):
        result = obj.get_clock()
        assert result["is_open"] is True

    # Should still be just 1 API call (all cache hits)
    assert mock_client.get_clock.call_count == 1
