"""
Rate limiting and throttling for API calls.

Prevents account bans from rate limit violations.
Implements exponential backoff and request queuing.

Pattern stolen from: Hummingbot async_utils.py
"""

import asyncio
import time
from collections import deque, defaultdict
from typing import Dict, Tuple, Callable, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """Rate limit configuration"""
    max_requests: int  # Maximum requests
    time_window: float  # Time window in seconds
    
    def __str__(self):
        return f"{self.max_requests} requests per {self.time_window}s"


class Throttler:
    """
    Centralized rate limiter for all external API calls.
    
    Prevents:
    - Broker bans from exceeding rate limits
    - Data provider throttling
    - Retry storms on failures
    
    Usage:
        throttler = Throttler({
            'alpaca_orders': RateLimit(200, 60.0),  # 200/min
            'polygon_data': RateLimit(5, 1.0),      # 5/sec
        })
        
        # Wrap calls:
        result = await throttler.execute(
            'alpaca_orders',
            broker.submit_order,
            symbol='SPY', qty=10
        )
    """
    
    def __init__(self, rate_limits: Dict[str, RateLimit]):
        """
        Args:
            rate_limits: Dict mapping limit_id to RateLimit config
        """
        self._rate_limits = rate_limits
        self._request_times: Dict[str, deque] = defaultdict(deque)
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        
        # Statistics
        self._total_requests: Dict[str, int] = defaultdict(int)
        self._total_waits: Dict[str, int] = defaultdict(int)
        self._total_wait_time: Dict[str, float] = defaultdict(float)
        
        logger.info(f"Throttler initialized with {len(rate_limits)} limits")
        for limit_id, limit in rate_limits.items():
            logger.info(f"  {limit_id}: {limit}")
    
    async def execute(
        self,
        limit_id: str,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute function respecting rate limits.
        
        Args:
            limit_id: Rate limit identifier
            func: Function to execute (can be sync or async)
            *args: Positional arguments to func
            **kwargs: Keyword arguments to func
            
        Returns:
            Result from func
            
        Raises:
            Exception from func if it fails
        """
        async with self._locks[limit_id]:
            # Wait if needed
            wait_time = await self._wait_if_needed(limit_id)
            
            # Record this request
            now = time.time()
            self._request_times[limit_id].append(now)
            self._total_requests[limit_id] += 1
            
            # Execute function
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Throttled call failed: {limit_id}",
                    extra={'error': str(e), 'func': func.__name__}
                )
                raise
    
    async def _wait_if_needed(self, limit_id: str) -> float:
        """
        Wait if at rate limit.
        
        Returns:
            Time waited in seconds (0 if no wait)
        """
        if limit_id not in self._rate_limits:
            # No limit defined, allow immediately
            return 0.0
        
        limit = self._rate_limits[limit_id]
        request_times = self._request_times[limit_id]
        
        # Remove old requests outside time window
        now = time.time()
        cutoff_time = now - limit.time_window
        
        while request_times and request_times[0] < cutoff_time:
            request_times.popleft()
        
        # Check if at limit
        if len(request_times) >= limit.max_requests:
            # Calculate wait time
            oldest_request = request_times[0]
            wait_time = (oldest_request + limit.time_window) - now
            
            if wait_time > 0:
                logger.warning(
                    f"Rate limit reached for {limit_id}, waiting {wait_time:.2f}s",
                    extra={
                        'limit_id': limit_id,
                        'wait_time': wait_time,
                        'limit': str(limit),
                        'current_requests': len(request_times)
                    }
                )
                
                self._total_waits[limit_id] += 1
                self._total_wait_time[limit_id] += wait_time
                
                await asyncio.sleep(wait_time)
                return wait_time
        
        return 0.0
    
    def get_stats(self, limit_id: Optional[str] = None) -> Dict:
        """
        Get throttling statistics.
        
        Args:
            limit_id: Specific limit to get stats for, or None for all
            
        Returns:
            Statistics dict
        """
        if limit_id:
            return {
                'limit_id': limit_id,
                'limit': str(self._rate_limits.get(limit_id, 'No limit')),
                'total_requests': self._total_requests[limit_id],
                'total_waits': self._total_waits[limit_id],
                'total_wait_time': self._total_wait_time[limit_id],
                'avg_wait_time': (
                    self._total_wait_time[limit_id] / self._total_waits[limit_id]
                    if self._total_waits[limit_id] > 0
                    else 0.0
                ),
                'current_window_requests': len(self._request_times[limit_id])
            }
        else:
            return {
                limit_id: self.get_stats(limit_id)
                for limit_id in self._rate_limits.keys()
            }
    
    def reset_stats(self):
        """Reset all statistics"""
        self._total_requests.clear()
        self._total_waits.clear()
        self._total_wait_time.clear()


class ExponentialBackoff:
    """
    Exponential backoff for retry logic.
    
    Usage:
        backoff = ExponentialBackoff(base=1.0, max_delay=60.0)
        
        for attempt in range(max_retries):
            try:
                result = await some_api_call()
                break
            except RateLimitError:
                delay = backoff.next_delay(attempt)
                await asyncio.sleep(delay)
    """
    
    def __init__(
        self,
        base: float = 1.0,
        multiplier: float = 2.0,
        max_delay: float = 60.0,
        jitter: bool = True
    ):
        """
        Args:
            base: Base delay in seconds
            multiplier: Exponential multiplier
            max_delay: Maximum delay cap
            jitter: Add random jitter to prevent thundering herd
        """
        self.base = base
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.jitter = jitter
        
    def next_delay(self, attempt: int) -> float:
        """
        Calculate next delay.
        
        Args:
            attempt: Attempt number (0-indexed)
            
        Returns:
            Delay in seconds
        """
        import random
        
        # Calculate exponential delay
        delay = min(self.base * (self.multiplier ** attempt), self.max_delay)
        
        # Add jitter (±25% of delay)
        if self.jitter and delay > 0:
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


# Pre-configured throttlers for common services
def create_alpaca_throttler() -> Throttler:
    """
    Create throttler for Alpaca API.
    
    Alpaca limits:
    - Orders: 200 requests/minute
    - Data: 200 requests/minute
    - Account: 200 requests/minute
    """
    return Throttler({
        'alpaca_orders': RateLimit(200, 60.0),
        'alpaca_data': RateLimit(200, 60.0),
        'alpaca_account': RateLimit(200, 60.0),
    })


def create_polygon_throttler() -> Throttler:
    """
    Create throttler for Polygon API.
    
    Polygon limits (basic tier):
    - 5 requests/second
    """
    return Throttler({
        'polygon_bars': RateLimit(5, 1.0),
        'polygon_quotes': RateLimit(5, 1.0),
        'polygon_trades': RateLimit(5, 1.0),
    })


def create_combined_throttler() -> Throttler:
    """
    Create throttler combining all services.
    """
    return Throttler({
        # Alpaca
        'alpaca_orders': RateLimit(200, 60.0),
        'alpaca_data': RateLimit(200, 60.0),
        'alpaca_account': RateLimit(200, 60.0),
        
        # Polygon
        'polygon_bars': RateLimit(5, 1.0),
        'polygon_quotes': RateLimit(5, 1.0),
        'polygon_trades': RateLimit(5, 1.0),
        
        # Finnhub
        'finnhub_quote': RateLimit(60, 60.0),  # 60/min free tier
        
        # Alpha Vantage
        'alpha_vantage': RateLimit(5, 60.0),  # 5/min free tier
        
        # Financial Modeling Prep
        'fmp': RateLimit(250, 86400.0),  # 250/day free tier
    })

    async def _wait_if_needed(self, limit_id: str) -> float:
        """
        Wait if at rate limit.

        Returns:
            Time waited in seconds (0 if no wait)
        """
        if limit_id not in self._rate_limits:
            # No limit defined, allow immediately
            return 0.0

        limit = self._rate_limits[limit_id]
        request_times = self._request_times[limit_id]

        # Remove old requests outside time window
        now = time.time()
        cutoff_time = now - limit.time_window

        while request_times and request_times[0] < cutoff_time:
            request_times.popleft()

        # Check if at limit
        if len(request_times) >= limit.max_requests:
            # Calculate wait time
            oldest_request = request_times[0]
            wait_time = (oldest_request + limit.time_window) - now

            if wait_time > 0:
                logger.warning(
                    f"Rate limit reached for {limit_id}, waiting {wait_time:.2f}s",
                    extra={
                        'limit_id': limit_id,
                        'wait_time': wait_time,
                        'limit': str(limit),
                        'current_requests': len(request_times)
                    }
                )

                self._total_waits[limit_id] += 1
                self._total_wait_time[limit_id] += wait_time

                await asyncio.sleep(wait_time)
                return wait_time

        return 0.0

    def get_stats(self, limit_id: Optional[str] = None) -> Dict:
        """
        Get throttling statistics.

        Args:
            limit_id: Specific limit to get stats for, or None for all

        Returns:
            Statistics dict
        """
        if limit_id:
            return {
                'limit_id': limit_id,
                'limit': str(self._rate_limits.get(limit_id, 'No limit')),
                'total_requests': self._total_requests[limit_id],
                'total_waits': self._total_waits[limit_id],
                'total_wait_time': self._total_wait_time[limit_id],
                'avg_wait_time': (
                    self._total_wait_time[limit_id] / self._total_waits[limit_id]
                    if self._total_waits[limit_id] > 0
                    else 0.0
                ),
                'current_window_requests': len(self._request_times[limit_id])
            }
        else:
            return {
                lid: self.get_stats(lid)
                for lid in self._rate_limits.keys()
            }

    def reset_stats(self) -> None:
        """Reset all statistics"""
        self._total_requests.clear()
        self._total_waits.clear()
        self._total_wait_time.clear()

class ExponentialBackoff:
    """
    Exponential backoff for retry logic.
    
    Usage:
        backoff = ExponentialBackoff(base=1.0, max_delay=60.0)
        
        for attempt in range(max_retries):
            try:
                result = await some_api_call()
                break
            except RateLimitError:
                delay = backoff.next_delay(attempt)
                await asyncio.sleep(delay)
    """
    
    def __init__(
        self,
        base: float = 1.0,
        multiplier: float = 2.0,
        max_delay: float = 60.0,
        jitter: bool = True
    ):
        """
        Args:
            base: Base delay in seconds
            multiplier: Exponential multiplier
            max_delay: Maximum delay cap
            jitter: Add random jitter to prevent thundering herd
        """
        self.base = base
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.jitter = jitter
        
    def next_delay(self, attempt: int) -> float:
        """
        Calculate next delay.
        
        Args:
            attempt: Attempt number (0-indexed)
            
        Returns:
            Delay in seconds
        """
        import random
        
        # Calculate exponential delay
        delay = min(self.base * (self.multiplier ** attempt), self.max_delay)
        
        # Add jitter (±25% of delay)
        if self.jitter and delay > 0:
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


# Pre-configured throttlers for common services
def create_alpaca_throttler() -> Throttler:
    """
    Create throttler for Alpaca API.
    
    Alpaca limits:
    - Orders: 200 requests/minute
    - Data: 200 requests/minute
    - Account: 200 requests/minute
    """
    return Throttler({
        'alpaca_orders': RateLimit(200, 60.0),
        'alpaca_data': RateLimit(200, 60.0),
        'alpaca_account': RateLimit(200, 60.0),
    })


def create_polygon_throttler() -> Throttler:
    """
    Create throttler for Polygon API.
    
    Polygon limits (basic tier):
    - 5 requests/second
    """
    return Throttler({
        'polygon_bars': RateLimit(5, 1.0),
        'polygon_quotes': RateLimit(5, 1.0),
        'polygon_trades': RateLimit(5, 1.0),
    })


def create_combined_throttler() -> Throttler:
    """
    Create throttler combining all services.
    """
    return Throttler({
        # Alpaca
        'alpaca_orders': RateLimit(200, 60.0),
        'alpaca_data': RateLimit(200, 60.0),
        'alpaca_account': RateLimit(200, 60.0),
        
        # Polygon
        'polygon_bars': RateLimit(5, 1.0),
        'polygon_quotes': RateLimit(5, 1.0),
        'polygon_trades': RateLimit(5, 1.0),
        
        # Finnhub
        'finnhub_quote': RateLimit(60, 60.0),  # 60/min free tier
        
        # Alpha Vantage
        'alpha_vantage': RateLimit(5, 60.0),  # 5/min free tier
        
        # Financial Modeling Prep
        'fmp': RateLimit(250, 86400.0),  # 250/day free tier
    })
