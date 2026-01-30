"""
SCANNER INTEGRATION PATCH - Universe Inbox Writer

OBJECTIVE:
Integrate scanner with two-gate universe system by writing high-scoring
candidates to data/universe/inbox.jsonl (Gate 1 output).

IMPLEMENTATION:
Add universe writer after candidate scoring, before alerts/export.

===============================================================================
PATCH LOCATION 1: Add imports at top of standalone_scanner.py
===============================================================================

After existing imports, add:

```python
# Universe integration (Gate 1)
try:
    from core.universe import get_scanner_adapter
    UNIVERSE_ENABLED = True
except ImportError:
    UNIVERSE_ENABLED = False
    print("WARNING: core.universe not available, universe integration disabled")
```

===============================================================================
PATCH LOCATION 2: Initialize adapter in ScannerEngine.__init__()
===============================================================================

In ScannerEngine.__init__(), after existing initialization, add:

```python
        # Universe integration (Gate 1)
        self.universe_adapter = None
        if UNIVERSE_ENABLED:
            try:
                self.universe_adapter = get_scanner_adapter()
                print("✓ Universe inbox writer enabled")
            except Exception as e:
                print(f"WARNING: Failed to init universe adapter: {e}")
```

===============================================================================
PATCH LOCATION 3: Write to inbox after candidate creation
===============================================================================

Find the section where candidates are created and alerts/exports happen.
This is likely in a method like `_scan_batch()` or `_process_candidates()`.

After a candidate is created with a score, add:

```python
        # Write high-scoring candidates to universe inbox (Gate 1)
        if self.universe_adapter and candidate.score >= 7.0:  # Threshold
            self._write_to_universe_inbox(candidate, session)
```

===============================================================================
PATCH LOCATION 4: Add helper method to ScannerEngine
===============================================================================

Add this method to ScannerEngine class:

```python
    def _write_to_universe_inbox(self, candidate: Candidate, session: str) -> None:
        """Write candidate to universe inbox (Gate 1)."""
        try:
            # Extract features from candidate
            features = {
                "rvol": candidate.vol_spike,
                "spread_bps": 10.0,  # Default if not available
                "dollar_vol": 10_000_000,  # Default if not available
                "atr_pct": candidate.atr_pct,
            }

            # Extract levels
            levels = {
                "hold": candidate.last,
                "break": candidate.vwap if candidate.vwap > 0 else candidate.last,
                "t1": candidate.last * 1.05,  # 5% target
                "t2": candidate.last * 1.10,  # 10% target
            }

            # Determine session ("pre" or "rth")
            session_code = "pre" if session.upper() == "PRE" else "rth"

            # Write to inbox
            success = self.universe_adapter.write_candidate(
                symbol=candidate.symbol,
                score=candidate.score,
                session=session_code,
                features=features,
                levels=levels,
                source="scanner_v2",
                version="2.1",
            )

            if success:
                print(f"  → Universe: {candidate.symbol} (score={candidate.score:.1f})")

        except Exception as e:
            print(f"WARNING: Failed to write {candidate.symbol} to universe: {e}")
```

===============================================================================
ENHANCED VERSION: With actual spread and dollar volume
===============================================================================

If your scanner has access to snapshot data with bid/ask/volume, use:

```python
    def _write_to_universe_inbox_enhanced(
        self,
        candidate: Candidate,
        session: str,
        snapshot: Optional[Dict] = None,
    ) -> None:
        """Write candidate to universe inbox with full metrics (Gate 1)."""
        try:
            # Calculate spread if snapshot available
            spread_bps = 10.0  # Default
            dollar_vol = 10_000_000  # Default

            if snapshot:
                bid = snapshot.get("bid", 0)
                ask = snapshot.get("ask", 0)
                last = snapshot.get("last", candidate.last)
                prev_day_vol = snapshot.get("prev_day_volume", 0)

                # Calculate spread in basis points
                if bid > 0 and ask > 0 and last > 0:
                    spread_bps = ((ask - bid) / last) * 10000

                # Calculate dollar volume
                if prev_day_vol > 0 and last > 0:
                    dollar_vol = prev_day_vol * last

            features = {
                "rvol": candidate.vol_spike,
                "spread_bps": spread_bps,
                "dollar_vol": dollar_vol,
                "atr_pct": candidate.atr_pct,
            }

            levels = {
                "hold": candidate.last,
                "break": candidate.vwap if candidate.vwap > 0 else candidate.last,
                "t1": candidate.last * 1.05,
                "t2": candidate.last * 1.10,
            }

            session_code = "pre" if session.upper() == "PRE" else "rth"

            success = self.universe_adapter.write_candidate(
                symbol=candidate.symbol,
                score=candidate.score,
                session=session_code,
                features=features,
                levels=levels,
                source="scanner_v2",
                version="2.1",
            )

            if success:
                print(f"  → Universe: {candidate.symbol} (score={candidate.score:.1f}, "
                      f"spread={spread_bps:.1f}bps, vol=${dollar_vol/1e6:.1f}M)")

        except Exception as e:
            print(f"WARNING: Failed to write {candidate.symbol} to universe: {e}")
```

===============================================================================
EXAMPLE INTEGRATION POINT
===============================================================================

Find code that looks like this in standalone_scanner.py:

```python
# Around line 1000-1200 (scoring section)
def score_candidate(symbol, bars, news, universe, session):
    # ... scoring logic ...
    
    return Candidate(
        symbol=symbol,
        last=last,
        score=float(score),
        # ... other fields ...
    )
```

After this, in the caller (likely ScannerEngine), add:

```python
# After candidate creation
candidate = score_candidate(symbol, bars, news, universe, session)

# NEW: Write to universe inbox if high score
if self.universe_adapter and candidate.score >= 7.0:
    self._write_to_universe_inbox(candidate, session)

# Existing alert/export logic continues...
if candidate.score >= self.cfg.alert_threshold:
    self._send_alert(candidate)
```

===============================================================================
TESTING
===============================================================================

1. Add the patches above to standalone_scanner.py

2. Run scanner:
```powershell
python -m scanners.standalone_scanner
```

3. Check inbox file:
```powershell
cat data\universe\inbox.jsonl
```

4. Verify JSON format:
```powershell
python -c "import json; [print(json.loads(line)) for line in open('data/universe/inbox.jsonl') if line.strip()]"
```

5. Expected output:
```
{"id": "2026-01-25T...", "symbol": "TSLA", "score": 8.5, ...}
{"id": "2026-01-25T...", "symbol": "NVDA", "score": 7.2, ...}
```

===============================================================================
CONFIGURATION
===============================================================================

Optional environment variables for scanner:

```bash
# Minimum score to write to universe (default: 7.0)
SCANNER_UNIVERSE_MIN_SCORE=7.0

# Enable/disable universe writing (default: 1)
SCANNER_UNIVERSE_ENABLED=1
```

Add to scanner config if desired:

```python
# In ScannerConfig or scanner __init__
self.universe_min_score = _env_float("SCANNER_UNIVERSE_MIN_SCORE", 7.0)
self.universe_enabled = _env_int("SCANNER_UNIVERSE_ENABLED", 1) == 1

# Then use:
if self.universe_enabled and candidate.score >= self.universe_min_score:
    self._write_to_universe_inbox(candidate, session)
```

===============================================================================
BACKWARD COMPATIBILITY
===============================================================================

This patch is 100% additive:
- No existing scanner functionality is changed
- If core.universe is not available, scanner continues normally
- If data/universe/ doesn't exist, adapter creates it
- Deduplication prevents spam

Scanner can run with or without universe integration.

===============================================================================
STATUS
===============================================================================

✅ Patch code ready
✅ Integration points identified
✅ Testing procedure defined
✅ Backward compatibility ensured

READY TO APPLY to standalone_scanner.py
"""

# Test the patch independently
if __name__ == "__main__":
    print("Testing universe scanner adapter...")
    
    try:
        from core.universe import get_scanner_adapter
        
        adapter = get_scanner_adapter()
        
        # Simulate a candidate
        success = adapter.write_candidate(
            symbol="TEST_SCAN",
            score=8.5,
            session="rth",
            features={
                "rvol": 3.2,
                "spread_bps": 8,
                "dollar_vol": 15000000,
                "atr_pct": 2.5,
            },
            levels={
                "hold": 245.0,
                "break": 250.0,
                "t1": 260.0,
                "t2": 275.0,
            },
        )
        
        print(f"✓ Write successful: {success}")
        print("✓ Check: data/universe/inbox.jsonl")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
