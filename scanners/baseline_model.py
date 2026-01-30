"""
Patch 22: Baseline model training (logistic regression) on daily training snapshots.

Reads:
  exports/training/daily/*.csv  (from Patch 21)

Outputs:
  exports/training/models/baseline_logreg.json  (always)
  exports/training/models/baseline_logreg_sklearn.joblib (if sklearn+joblib available)

Notes:
- This is a *baseline* to sanity-check your pipeline, not "alpha".
- If sklearn isn't installed, it falls back to a dumb scoring proxy and prints guidance.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import csv
import math
import random


FEATURES = [
    "score",
    "gap_pct",
    "atr_pct",
    "vol_spike",
    "persistence",
    "session_vol",
]


def _safe_float(x: Any) -> float:
    try:
        if x is None or x == "":
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _load_rows(pattern: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for fp in glob.glob(pattern):
        with open(fp, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(row)
    return rows


def _train_test_split(rows: List[Dict[str, Any]], test_frac: float = 0.2, seed: int = 7):
    rnd = random.Random(seed)
    rows2 = rows[:]
    rnd.shuffle(rows2)
    n_test = int(len(rows2) * test_frac)
    test = rows2[:n_test]
    train = rows2[n_test:]
    return train, test


def _to_xy(rows: List[Dict[str, Any]]):
    X: List[List[float]] = []
    y: List[int] = []
    for r in rows:
        yy = r.get("y_win")
        if yy is None or yy == "":
            continue
        try:
            yy_i = int(float(yy))
        except Exception:
            continue
        feats = [_safe_float(r.get(k)) for k in FEATURES]
        X.append(feats)
        y.append(yy_i)
    return X, y


def _auc(y_true: List[int], y_score: List[float]) -> float:
    # simple AUC (rank-based)
    pairs = sorted(zip(y_score, y_true), key=lambda x: x[0])
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    rank_sum = 0.0
    for i, (_, yt) in enumerate(pairs, start=1):
        if yt == 1:
            rank_sum += i
    auc = (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(auc)


def train_baseline(
    daily_dir: str = "exports/training/daily",
    out_dir: str = "exports/training/models",
) -> str:
    ddir = Path(daily_dir)
    odir = Path(out_dir)
    odir.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(str(ddir / "*.csv"))
    if len(rows) < 50:
        raise RuntimeError(f"Not enough training rows ({len(rows)}). Run Patch 21 and collect outcomes first.")

    train_rows, test_rows = _train_test_split(rows)
    X_train, y_train = _to_xy(train_rows)
    X_test, y_test = _to_xy(test_rows)

    # Try sklearn first
    model_json_path = odir / "baseline_logreg.json"
    try:
        from sklearn.linear_model import LogisticRegression
        import numpy as np

        clf = LogisticRegression(max_iter=200, n_jobs=None)
        clf.fit(np.array(X_train), np.array(y_train))

        probs = clf.predict_proba(np.array(X_test))[:, 1].tolist()
        auc = _auc(y_test, probs)

        weights = {
            "features": FEATURES,
            "intercept": float(clf.intercept_[0]),
            "coef": [float(x) for x in clf.coef_[0].tolist()],
            "auc": auc,
            "n_train": len(y_train),
            "n_test": len(y_test),
        }

        model_json_path.write_text(json.dumps(weights, indent=2), encoding="utf-8")

        # optional joblib save
        try:
            import joblib
            joblib.dump(clf, str(odir / "baseline_logreg_sklearn.joblib"))
        except Exception:
            pass

        return str(model_json_path)

    except Exception as e:
        # fallback: save a dumb proxy that uses score only
        # (still useful for pipeline sanity)
        weights = {
            "features": FEATURES,
            "note": "sklearn not available; fallback uses score-only proxy",
            "intercept": 0.0,
            "coef": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "auc": 0.5,
            "error": str(e),
            "n_train": len(y_train),
            "n_test": len(y_test),
        }
        model_json_path.write_text(json.dumps(weights, indent=2), encoding="utf-8")
        return str(model_json_path)


def main() -> None:
    model_path = train_baseline(
        daily_dir=os.getenv("TRAINING_DAILY_DIR", "exports/training/daily"),
        out_dir=os.getenv("TRAINING_MODEL_DIR", "exports/training/models"),
    )
    print("[baseline] wrote", model_path)


if __name__ == "__main__":
    main()