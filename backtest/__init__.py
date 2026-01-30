"""
Backtesting components - LEAN-grade.
"""

from .engine import BacktestEngine

from .fill_models import (
    FillModel,
    ImmediateFillModel,
    SlippageModel,
    ConstantSlippageModel,
    VolumeShareSlippageModel,
    AssetClass,
    OrderType,
)

from .fee_models import (
    FeeModel,
    InteractiveBrokersFeeModel,
    AlpacaFeeModel,
    ConstantFeeModel,
    ZeroFeeModel,
)

from .data_handler import HistoricalDataHandler

from .simulated_broker import SimulatedBroker, SimulatedOrder

from .performance import PerformanceAnalyzer, PerformanceMetrics

from .results import ResultsFormatter

__all__ = [
    "BacktestEngine",
    "FillModel",
    "ImmediateFillModel",
    "SlippageModel",
    "ConstantSlippageModel",
    "VolumeShareSlippageModel",
    "AssetClass",
    "OrderType",
    "FeeModel",
    "InteractiveBrokersFeeModel",
    "AlpacaFeeModel",
    "ConstantFeeModel",
    "ZeroFeeModel",
    "HistoricalDataHandler",
    "SimulatedBroker",
    "SimulatedOrder",
    "PerformanceAnalyzer",
    "PerformanceMetrics",
    "ResultsFormatter",
]
