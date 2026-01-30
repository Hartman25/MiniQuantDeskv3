"""
Scanner Integration Patch - Write Candidates to Universe Inbox

INTEGRATION POINT:
Add this code to standalone_scanner.py where high-scoring symbols are identified.

LOCATION:
In the scanner's main loop, after a symbol passes scoring/filtering,
call scanner_adapter.write_candidate() to send it to Gate 2.

EXAMPLE INTEGRATION:
```python
# At top of standalone_scanner.py
from core.universe import get_scanner_adapter

# In scanner __init__ or main():
scanner_adapter = get_scanner_adapter()

# In scan loop, after symbol passes filters:
if score >= 7.0:  # Your threshold
    scanner_adapter.write_candidate(
        symbol=symbol,
        score=score,
        session="pre" if is_premarket else "rth",
        features={
            "rvol": relative_volume,
            "gap_pct": gap_percent,
            "spread_bps": spread_basis_points,
            "pm_vol": premarket_volume,
            "dollar_vol": daily_dollar_volume,
            "atr_pct": atr_percentage,
        },
        levels={
            "hold": support_level,
            "break": resistance_level,
            "t1": target1,
            "t2": target2,
        },
    )
```

MINIMAL INTEGRATION:
If scanner doesn't have all metrics, minimum required:
```python
scanner_adapter.write_candidate(
    symbol=symbol,
    score=score,
    features={"dollar_vol": daily_dollar_volume},  # Minimum
    levels={"hold": current_price},  # Minimum
)
```

DEDUPLICATION:
- Scanner adapter automatically deduplicates within 5 minutes
- Same symbol won't be written unless 5+ minutes elapsed
- No need to track this in scanner code

SESSION DETECTION:
```python
def get_current_session():
    now = datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour
    if 4 <= hour < 9:
        return "pre"
    elif 9 <= hour < 16:
        return "rth"
    else:
        return "closed"
```

FULL FEATURE MAPPING:
Scanner metric → inbox feature key
- Relative volume → "rvol"
- Gap percent → "gap_pct"
- Spread (bps) → "spread_bps"
- Premarket volume → "pm_vol"
- Daily $ volume → "dollar_vol"
- ATR percent → "atr_pct"
- Any other metrics → add as needed

TESTING:
1. Run scanner with integration enabled
2. Check data/universe/inbox.jsonl for new entries
3. Each line should be valid JSON with all fields
4. Verify deduplication (same symbol won't appear twice in 5 min)

BACKWARD COMPATIBILITY:
- This integration is additive (doesn't break existing scanner)
- Scanner can run with or without universe system
- If data/universe/ doesn't exist, adapter creates it
"""

# Example standalone test script
if __name__ == "__main__":
    from pathlib import Path
    from core.universe import get_scanner_adapter

    # Get adapter
    adapter = get_scanner_adapter()

    # Test write
    success = adapter.write_candidate(
        symbol="TSLA",
        score=8.5,
        session="rth",
        features={
            "rvol": 3.2,
            "gap_pct": 5.1,
            "spread_bps": 8,
            "dollar_vol": 15000000,
        },
        levels={
            "hold": 245.00,
            "break": 250.00,
            "t1": 260.00,
            "t2": 275.00,
        },
    )

    print(f"Write success: {success}")
    print(f"Check: data/universe/inbox.jsonl")
