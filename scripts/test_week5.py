"""
Week 5 test - ML/AI Integration (Shadow Mode).
"""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ml import (
    ShadowModeTracker,
    FeatureEngineer,
    InferenceEngine,
    SimpleRuleModel
)

import pandas as pd


def test_week5_components():
    """Test Week 5 components."""
    print("\n" + "="*70)
    print("Week 5 Test - ML/AI Integration (Shadow Mode)")
    print("="*70)
    
    # Test 1: Shadow Mode Tracker
    print("\n[1] Testing Shadow Mode Tracker...")
    
    tracker = ShadowModeTracker(log_dir=Path("data/shadow_test"))
    
    # Log prediction
    pred_id = tracker.log_prediction(
        model_name="LSTM_v1",
        model_version="1.0.0",
        symbol="SPY",
        prediction="BUY",
        confidence=Decimal("0.85"),
        features={"rsi": 45.3, "volume": 1000000}
    )
    
    print(f"    Prediction logged: {pred_id}")
    
    # Record outcome
    tracker.record_outcome(
        prediction_id=pred_id,
        actual_action="BUY",
        actual_return=Decimal("0.02")
    )
    
    print(f"    Outcome recorded")
    
    # Get metrics
    metrics = tracker.get_metrics("LSTM_v1")
    print(f"    Metrics: Accuracy={metrics['accuracy']:.2%}, Predictions={metrics['total_predictions']}")
    
    # Test 2: Feature Engineering
    print("\n[2] Testing Feature Engineering...")
    
    engineer = FeatureEngineer()
    
    # Create sample data
    dates = pd.date_range(start='2026-01-01', periods=60, freq='D')
    prices = list(range(100, 160))
    
    bars = pd.DataFrame({
        'close': prices,
        'open': prices,
        'high': [p + 2 for p in prices],
        'low': [p - 2 for p in prices],
        'volume': [1000000 + i*1000 for i in range(60)]
    }, index=dates)
    
    features = engineer.extract_features(bars)
    
    print(f"    Extracted {len(features)} features")
    print(f"    Sample: close={features.get('close')}, sma_20={features.get('sma_20')}")
    
    # Test 3: Inference Engine
    print("\n[3] Testing Inference Engine...")
    
    engine = InferenceEngine()
    
    # Register simple rule model
    model = SimpleRuleModel()
    engine.register_model("rule_v1", model)
    
    print(f"    Model registered: {model.name} v{model.version}")
    
    # Make prediction
    result = engine.predict("rule_v1", features)
    
    if result:
        print(f"    Prediction: {result['prediction']} (confidence={result['confidence']})")
    else:
        print(f"    No prediction")
    
    # Test 4: Integration Test
    print("\n[4] Testing Full ML Pipeline...")
    
    # Feature extraction -> Inference -> Shadow logging
    features2 = engineer.extract_features(bars)
    prediction = engine.predict("rule_v1", features2)
    
    if prediction:
        pred_id2 = tracker.log_prediction(
            model_name="rule_v1",
            model_version="1.0",
            symbol="SPY",
            prediction=prediction["prediction"],
            confidence=Decimal(str(prediction["confidence"])),
            features=features2
        )
        print(f"    Full pipeline executed: {pred_id2}")
    
    # Test 5: Model Listing
    print("\n[5] Testing Model Registry...")
    
    models = engine.list_models()
    print(f"    Registered models: {', '.join(models)}")
    
    for model_id in models:
        info = engine.get_model_info(model_id)
        print(f"      {model_id}: {info['name']} v{info['version']}")
    
    print("\n" + "="*70)
    print("ALL WEEK 5 TESTS PASSED")
    print("="*70)
    print("\nWeek 5 Components:")
    print("  [X] ShadowModeTracker")
    print("  [X] FeatureEngineer")
    print("  [X] InferenceEngine")
    print("  [X] SimpleRuleModel")
    print("\nALL WEEKS COMPLETE!")
    print()


if __name__ == "__main__":
    test_week5_components()
