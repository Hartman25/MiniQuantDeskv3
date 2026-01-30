"""
Discord integration components.
"""

from .notifier import (
    DiscordNotifier,
    NotificationChannel,
    NotificationPriority,
)

from .bot import (
    TradingBot,
    SystemController,
)

from .summary import (
    DailySummaryGenerator,
    DailyStats,
)

from .bridge import (
    DiscordEventBridge,
)

__all__ = [
    "DiscordNotifier",
    "NotificationChannel",
    "NotificationPriority",
    "TradingBot",
    "SystemController",
    "DailySummaryGenerator",
    "DailyStats",
    "DiscordEventBridge",
]
