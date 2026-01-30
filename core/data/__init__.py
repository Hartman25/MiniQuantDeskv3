"""
Market data package.

Keep __init__ lightweight.
Provider-specific or optional dependencies must NOT break unit tests.

Safe import:
- core.data.contract.MarketDataContract

Optional import:
- core.data.pipeline.MarketDataPipeline and related types
"""

try:
    from .pipeline import (
        MarketDataPipeline,
        DataProvider,
        BarData,
        DataPipelineError,
        DataStalenessError,
    )

    __all__ = [
        "MarketDataPipeline",
        "DataProvider",
        "BarData",
        "DataPipelineError",
        "DataStalenessError",
    ]
except Exception:  # pragma: no cover
    __all__ = []
