"""
Shadow mode - ML predictions without affecting live trading.

ARCHITECTURE:
- Parallel prediction logging
- Performance tracking
- Model comparison
- Zero impact on live trading
- Feature versioning

Based on A/B testing and shadow deployment patterns.
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, asdict
import json
from pathlib import Path

from core.logging import get_logger, LogStream


# ============================================================================
# SHADOW PREDICTION
# ============================================================================

@dataclass
class ShadowPrediction:
    """ML prediction in shadow mode."""
    prediction_id: str
    model_name: str
    model_version: str
    symbol: str
    prediction: str  # "BUY", "SELL", "HOLD"
    confidence: Decimal
    features: Dict[str, float]
    timestamp: datetime
    
    # Outcome tracking
    actual_action: Optional[str] = None
    actual_return: Optional[Decimal] = None
    correct: Optional[bool] = None
    
    def to_dict(self) -> Dict:
        """Convert to dict."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["confidence"] = str(self.confidence)
        if self.actual_return:
            data["actual_return"] = str(self.actual_return)
        return data


# ============================================================================
# SHADOW MODE TRACKER
# ============================================================================

class ShadowModeTracker:
    """
    Shadow mode prediction tracker.
    
    FEATURES:
    - Log predictions without execution
    - Track outcomes
    - Compare to live strategy
    - Performance metrics
    - Model versioning
    
    USAGE:
        tracker = ShadowModeTracker(log_dir=Path("data/shadow"))
        
        # Log prediction
        pred_id = tracker.log_prediction(
            model_name="LSTM_v1",
            symbol="SPY",
            prediction="BUY",
            confidence=0.85,
            features={"rsi": 45.3, "volume": 1000000}
        )
        
        # Later: record outcome
        tracker.record_outcome(
            prediction_id=pred_id,
            actual_action="BUY",
            actual_return=Decimal("0.02")
        )
        
        # Get metrics
        metrics = tracker.get_metrics("LSTM_v1")
    """
    
    def __init__(self, log_dir: Path):
        """Initialize tracker."""
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = get_logger(LogStream.STRATEGY)
        
        # In-memory cache
        self._predictions: Dict[str, ShadowPrediction] = {}
        
        # Log file
        self.log_file = self.log_dir / f"shadow_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        self.logger.info("ShadowModeTracker initialized", extra={
            "log_dir": str(log_dir)
        })
    
    def log_prediction(
        self,
        model_name: str,
        model_version: str,
        symbol: str,
        prediction: str,
        confidence: Decimal,
        features: Dict[str, float]
    ) -> str:
        """
        Log ML prediction.
        
        Args:
            model_name: Model identifier
            model_version: Model version
            symbol: Stock symbol
            prediction: "BUY", "SELL", "HOLD"
            confidence: Prediction confidence (0-1)
            features: Feature dict
            
        Returns:
            prediction_id
        """
        prediction_id = f"{model_name}_{symbol}_{int(datetime.now().timestamp()*1000)}"
        
        pred = ShadowPrediction(
            prediction_id=prediction_id,
            model_name=model_name,
            model_version=model_version,
            symbol=symbol,
            prediction=prediction,
            confidence=confidence,
            features=features,
            timestamp=datetime.now()
        )
        
        # Cache
        self._predictions[prediction_id] = pred
        
        # Write to log
        self._write_log(pred)
        
        self.logger.info(f"Shadow prediction logged: {model_name}", extra={
            "prediction_id": prediction_id,
            "symbol": symbol,
            "prediction": prediction,
            "confidence": str(confidence)
        })
        
        return prediction_id
    
    def record_outcome(
        self,
        prediction_id: str,
        actual_action: str,
        actual_return: Optional[Decimal] = None
    ):
        """
        Record actual outcome.
        
        Args:
            prediction_id: Prediction ID
            actual_action: Actual action taken
            actual_return: Actual return (if available)
        """
        pred = self._predictions.get(prediction_id)
        if not pred:
            self.logger.warning(f"Unknown prediction: {prediction_id}")
            return
        
        pred.actual_action = actual_action
        pred.actual_return = actual_return
        pred.correct = (pred.prediction == actual_action)
        
        # Write updated log
        self._write_log(pred)
        
        self.logger.info(f"Outcome recorded: {prediction_id}", extra={
            "correct": pred.correct,
            "predicted": pred.prediction,
            "actual": actual_action
        })
    
    def get_metrics(self, model_name: Optional[str] = None) -> Dict:
        """
        Get performance metrics.
        
        Args:
            model_name: Filter by model (None = all models)
            
        Returns:
            Dict with metrics
        """
        predictions = list(self._predictions.values())
        
        if model_name:
            predictions = [p for p in predictions if p.model_name == model_name]
        
        # Filter only predictions with outcomes
        completed = [p for p in predictions if p.actual_action is not None]
        
        if not completed:
            return {
                "total_predictions": len(predictions),
                "completed": 0,
                "accuracy": 0,
                "avg_confidence": 0
            }
        
        correct = sum(1 for p in completed if p.correct)
        avg_conf = sum(float(p.confidence) for p in completed) / len(completed)
        
        # Calculate return metrics if available
        returns = [p.actual_return for p in completed if p.actual_return is not None]
        avg_return = sum(returns) / len(returns) if returns else None
        
        return {
            "model_name": model_name,
            "total_predictions": len(predictions),
            "completed": len(completed),
            "accuracy": correct / len(completed),
            "avg_confidence": avg_conf,
            "avg_return": str(avg_return) if avg_return else None
        }
    
    def _write_log(self, pred: ShadowPrediction):
        """Write prediction to log file."""
        with open(self.log_file, "a") as f:
            f.write(json.dumps(pred.to_dict()) + "\n")


# ============================================================================
# FEATURE ENGINEERING
# ============================================================================

class FeatureEngineer:
    """
    Feature engineering for ML models.
    
    FEATURES:
    - Technical indicators
    - Price transforms
    - Volume features
    - Time features
    
    USAGE:
        engineer = FeatureEngineer()
        
        features = engineer.extract_features(bars)
    """
    
    def __init__(self):
        """Initialize engineer."""
        self.logger = get_logger(LogStream.DATA)
    
    def extract_features(self, bars: 'pd.DataFrame') -> Dict[str, float]:
        """
        Extract features from bars.
        
        Args:
            bars: OHLCV DataFrame
            
        Returns:
            Feature dict
        """
        if bars.empty:
            return {}
        
        features = {}
        
        # Price features
        features["close"] = float(bars["close"].iloc[-1])
        features["open"] = float(bars["open"].iloc[-1])
        features["high"] = float(bars["high"].iloc[-1])
        features["low"] = float(bars["low"].iloc[-1])
        
        # Returns
        returns = bars["close"].pct_change()
        features["return_1"] = float(returns.iloc[-1]) if len(returns) > 0 else 0
        features["return_5"] = float(returns.tail(5).mean()) if len(returns) >= 5 else 0
        
        # Volume
        features["volume"] = float(bars["volume"].iloc[-1])
        features["volume_ma_20"] = float(bars["volume"].tail(20).mean()) if len(bars) >= 20 else 0
        
        # Simple moving averages
        if len(bars) >= 20:
            features["sma_20"] = float(bars["close"].tail(20).mean())
        
        if len(bars) >= 50:
            features["sma_50"] = float(bars["close"].tail(50).mean())
        
        return features
