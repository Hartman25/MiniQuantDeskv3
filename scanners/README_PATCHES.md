# Scanner bundle (Patches 18, 21, 22 + cache hardening)

## Files
- `scanners/standalone_scanner.py` (includes Patch 18 signal JSONL + safer export)
- `scanners/universe_builder.py` (cache auto-bust on age + filter changes)
- `scanners/signal_consumer.py` (bot-side tail consumer)
- `scanners/signal_outcomes.py` (bot-side outcomes logger)
- `scanners/training_dataset_builder.py` (Patch 21)
- `scanners/baseline_model.py` (Patch 22)

## New/important env vars
- `SIGNALS_JSONL_PATH=exports/scanner_signals.jsonl`
- `WATCHLIST_TXT_PATH=exports/scanner_watchlist.txt`
- `UNIVERSE_CACHE_MAX_AGE_HOURS=6`
- `UNIVERSE_CACHE_BUST_ON_FILTER_CHANGE=1`

## Dependencies
- `requests`
- `tzdata` (needed on some Windows Python builds for `zoneinfo`)
- Optional for Patch 22 baseline: `scikit-learn`, `numpy`, `joblib`

## Run
- Scanner: `python -m scanners.standalone_scanner`
- Build training snapshots: `python -m scanners.training_dataset_builder`
- Train baseline model: `python -m scanners.baseline_model`
