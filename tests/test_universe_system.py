"""
Two-Gate Universe System - End-to-End Test

Tests complete flow:
1. Scanner writes candidates to inbox.jsonl (Gate 1)
2. Bot processes candidates and writes decisions (Gate 2)
3. Universe snapshot is updated
4. Trading bot loads universe

Usage:
  python test_universe_system.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

from core.universe import (
    get_scanner_adapter,
    UniverseInboxProcessor,
    get_universe_symbols,
)
from core.time import RealTimeClock


def test_gate1_scanner_output():
    """Test Gate 1: Scanner writing to inbox"""
    print("\n" + "="*70)
    print("TEST 1: GATE 1 (Scanner Output)")
    print("="*70)
    
    adapter = get_scanner_adapter()
    
    # Write test candidates
    candidates = [
        {
            "symbol": "TSLA",
            "score": 8.5,
            "session": "rth",
            "features": {
                "rvol": 3.2,
                "gap_pct": 5.1,
                "spread_bps": 8,
                "dollar_vol": 15000000,
                "atr_pct": 2.5,
            },
            "levels": {
                "hold": 245.00,
                "break": 250.00,
                "t1": 260.00,
                "t2": 275.00,
            },
        },
        {
            "symbol": "NVDA",
            "score": 7.8,
            "session": "rth",
            "features": {
                "rvol": 2.8,
                "spread_bps": 6,
                "dollar_vol": 20000000,
                "atr_pct": 3.0,
            },
            "levels": {
                "hold": 520.00,
                "break": 525.00,
                "t1": 540.00,
                "t2": 560.00,
            },
        },
        {
            "symbol": "AAPL",
            "score": 6.2,  # Below threshold
            "session": "rth",
            "features": {
                "rvol": 1.5,
                "spread_bps": 85,  # High spread - will be rejected
                "dollar_vol": 30000000,
            },
            "levels": {"hold": 185.00},
        },
    ]
    
    for cand in candidates:
        success = adapter.write_candidate(**cand)
        status = "✓" if success else "✗"
        print(f"{status} Wrote {cand['symbol']} (score={cand['score']})")
    
    print(f"\n✓ Test 1 complete: Check data/universe/inbox.jsonl")


def test_gate2_processing():
    """Test Gate 2: Bot processing candidates"""
    print("\n" + "="*70)
    print("TEST 2: GATE 2 (Bot Processing)")
    print("="*70)
    
    clock = RealTimeClock()
    data_dir = Path("data/universe")
    
    processor = UniverseInboxProcessor(
        data_dir=data_dir,
        clock=clock,
        broker_connector=None,  # No broker for test
    )
    
    # Process candidates
    decisions = processor.process_new_candidates(
        has_open_position=False,
        has_open_orders=False,
    )
    
    print(f"\nProcessed {len(decisions)} candidates:")
    for d in decisions:
        status = "✓ ACCEPT" if d.decision == "accept" else "✗ REJECT"
        print(f"  {status}: {d.symbol} ({d.reason})")
    
    print(f"\n✓ Test 2 complete: Check data/universe/decisions.jsonl")


def test_universe_snapshot():
    """Test universe snapshot reading"""
    print("\n" + "="*70)
    print("TEST 3: UNIVERSE SNAPSHOT")
    print("="*70)
    
    universe_path = Path("data/universe/universe_active.json")
    
    if not universe_path.exists():
        print("✗ universe_active.json not found")
        return
    
    with open(universe_path) as f:
        data = json.load(f)
    
    print(f"\nUniverse snapshot:")
    print(f"  CORE: {data.get('core', [])}")
    print(f"  ACCEPTED: {data.get('accepted', [])}")
    print(f"  TOTAL: {len(data.get('core', [])) + len(data.get('accepted', []))}")
    
    expires = data.get("expires_by_symbol", {})
    if expires:
        print(f"\n  Expiration times:")
        for symbol, exp_time in expires.items():
            print(f"    {symbol}: {exp_time}")
    
    print(f"\n✓ Test 3 complete")


def test_universe_loading():
    """Test trading bot loading universe"""
    print("\n" + "="*70)
    print("TEST 4: UNIVERSE LOADING (Trading Bot)")
    print("="*70)
    
    # Test different modes
    for mode in ["hybrid", "scanner", "accepted"]:
        symbols = get_universe_symbols(mode=mode)
        print(f"\n  Mode '{mode}': {symbols}")
    
    print(f"\n✓ Test 4 complete")


def test_file_formats():
    """Verify file formats are correct"""
    print("\n" + "="*70)
    print("TEST 5: FILE FORMAT VALIDATION")
    print("="*70)
    
    data_dir = Path("data/universe")
    
    # Check inbox.jsonl
    inbox_path = data_dir / "inbox.jsonl"
    if inbox_path.exists():
        print(f"\n✓ inbox.jsonl exists ({inbox_path.stat().st_size} bytes)")
        
        with open(inbox_path) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
        if lines:
            first = json.loads(lines[0])
            print(f"  Sample: {first['symbol']} (score={first.get('score')})")
            required = ["id", "ts", "symbol", "score", "features", "levels"]
            missing = [k for k in required if k not in first]
            if missing:
                print(f"  ✗ Missing fields: {missing}")
            else:
                print(f"  ✓ All required fields present")
    
    # Check decisions.jsonl
    decisions_path = data_dir / "decisions.jsonl"
    if decisions_path.exists():
        print(f"\n✓ decisions.jsonl exists ({decisions_path.stat().st_size} bytes)")
        
        with open(decisions_path) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
        if lines:
            first = json.loads(lines[0])
            print(f"  Sample: {first['symbol']} ({first.get('decision')})")
            required = ["id", "ts", "symbol", "decision", "reason"]
            missing = [k for k in required if k not in first]
            if missing:
                print(f"  ✗ Missing fields: {missing}")
            else:
                print(f"  ✓ All required fields present")
    
    # Check universe_active.json
    universe_path = data_dir / "universe_active.json"
    if universe_path.exists():
        print(f"\n✓ universe_active.json exists ({universe_path.stat().st_size} bytes)")
        
        with open(universe_path) as f:
            data = json.load(f)
        
        required = ["core", "accepted", "expires_by_symbol", "last_updated"]
        missing = [k for k in required if k not in data]
        if missing:
            print(f"  ✗ Missing fields: {missing}")
        else:
            print(f"  ✓ All required fields present")
    
    print(f"\n✓ Test 5 complete")


def cleanup_test_data():
    """Clean up test data (optional)"""
    print("\n" + "="*70)
    print("CLEANUP (optional)")
    print("="*70)
    
    print("\nTo clean up test data, run:")
    print("  del data\\universe\\inbox.jsonl")
    print("  del data\\universe\\decisions.jsonl")
    print("  python -c \"import json; from pathlib import Path; p = Path('data/universe/universe_active.json'); p.write_text(json.dumps({'core': ['SPY', 'QQQ'], 'accepted': [], 'expires_by_symbol': {}}, indent=2))\"")


if __name__ == "__main__":
    print("\n" + "#"*70)
    print("# TWO-GATE UNIVERSE SYSTEM - END-TO-END TEST")
    print("#"*70)
    
    try:
        # Run all tests
        test_gate1_scanner_output()
        test_gate2_processing()
        test_universe_snapshot()
        test_universe_loading()
        test_file_formats()
        cleanup_test_data()
        
        print("\n" + "="*70)
        print("✓ ALL TESTS COMPLETE")
        print("="*70)
        print("\nNext steps:")
        print("1. Integrate scanner (see scanners/SCANNER_INTEGRATION_PATCH.py)")
        print("2. Integrate trading bot (see TRADING_BOT_INTEGRATION_PATCH.py)")
        print("3. Run full system:")
        print("   - Terminal 1: python -m scanners.standalone_scanner")
        print("   - Terminal 2: python -m core.universe.daemon")
        print("   - Terminal 3: python entry_paper.py")
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
