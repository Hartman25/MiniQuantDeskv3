Scanner patches bundle

This zip contains updated scanner files:
- scanners/standalone_scanner.py (Patch 18: signal JSONL + watchlist TXT)
- scanners/universe_builder.py (cache auto-bust: max-age + filter signature)
- scanners/training_dataset_builder.py (Patch 21: merges signals+outcomes into daily CSV)
- scanners/baseline_model.py (Patch 22: baseline logistic regression if sklearn installed)
- scanners/signal_consumer.py
- scanners/signal_outcomes.py

Install deps in your venv:
  pip install -r requirements_scanner.txt

Run scanner:
  python -m scanners.standalone_scanner

Build training data (after you have outcomes):
  python scanners/training_dataset_builder.py

Train baseline model:
  python scanners/baseline_model.py
