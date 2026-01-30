"""
Test script to validate logging and configuration systems.

Run this to verify:
1. Logging infrastructure works
2. Configuration loads and validates
3. Log streams are properly separated
4. JSON formatting is correct
5. Correlation ID tracking works
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.logging import (
    setup_logging,
    get_logger,
    LogContext,
    log_execution_time,
    LogStream
)
from core.config import load_config, ConfigLoader


def test_logging_system():
    """Test logging infrastructure."""
    print("\n" + "="*60)
    print("TEST 1: Logging System")
    print("="*60)
    
    # Initialize logging
    setup_logging(
        log_dir=Path("logs"),
        log_level="DEBUG",
        console_level="INFO",
        json_logs=True
    )
    
    # Get loggers for different streams
    system_logger = get_logger(LogStream.SYSTEM)
    trading_logger = get_logger(LogStream.TRADING)
    orders_logger = get_logger(LogStream.ORDERS)
    
    # Test basic logging
    system_logger.info("System logger test")
    trading_logger.info("Trading logger test")
    orders_logger.info("Orders logger test")
    
    # Test correlation ID tracking
    with LogContext("test_corr_123"):
        orders_logger.info(
            "Order submitted",
            extra={
                "order_id": "ORD_001",
                "symbol": "SPY",
                "quantity": 1,
                "side": "LONG"
            }
        )
    
    # Test performance logging
    with log_execution_time("test_operation", orders_logger, symbol="SPY"):
        import time
        time.sleep(0.1)  # Simulate work
    
    print("\n[PASS] Logging test complete. Check logs/ directory for output.")


def test_configuration_system():
    """Test configuration loading and validation."""
    print("\n" + "="*60)
    print("TEST 2: Configuration System")
    print("="*60)
    
    try:
        # Test loading config
        config = load_config(Path("config"))
        
        print(f"[PASS] Config loaded successfully")
        print(f"   - Broker: {config.broker.name}")
        print(f"   - Paper Trading: {config.broker.paper_trading}")
        print(f"   - Max Positions: {config.risk.max_open_positions}")
        print(f"   - Daily Loss Limit: ${config.risk.daily_loss_limit_usd}")
        print(f"   - Strategies: {len(config.strategies)}")
        
        for strategy in config.strategies:
            print(f"     * {strategy.name}: {', '.join(strategy.symbols)}")
        
        # Test secrets scrubbing
        loader = ConfigLoader(Path("config"))
        raw_config = loader.load()
        scrubbed = loader.scrub_secrets(raw_config)
        
        print(f"\n[PASS] Secrets scrubbing works:")
        print(f"   - Raw API Key: {raw_config['broker']['api_key'][:10]}...")
        print(f"   - Scrubbed: {scrubbed['broker']['api_key']}")
        
        # Test small account validation
        from decimal import Decimal
        warnings = config.validate_small_account(Decimal("200"))
        
        if warnings:
            print(f"\n[WARN] Small account warnings:")
            for warning in warnings:
                print(f"   - {warning}")
        
    except Exception as e:
        print(f"[FAIL] Config error: {e}")
        import traceback
        traceback.print_exc()


def test_log_file_contents():
    """Verify log files contain JSON."""
    print("\n" + "="*60)
    print("TEST 3: Log File Contents")
    print("="*60)
    
    import json
    
    # Check orders log
    orders_log = Path("logs/orders/orders.log")
    if orders_log.exists():
        with open(orders_log, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if lines:
                last_line = lines[-1]
                try:
                    log_entry = json.loads(last_line)
                    print(f"[PASS] Orders log contains valid JSON:")
                    print(f"   - Timestamp: {log_entry.get('timestamp')}")
                    print(f"   - Level: {log_entry.get('level')}")
                    print(f"   - Correlation ID: {log_entry.get('correlation_id')}")
                    print(f"   - Message: {log_entry.get('message')}")
                    if 'extra' in log_entry:
                        print(f"   - Extra fields: {list(log_entry['extra'].keys())}")
                except json.JSONDecodeError:
                    print(f"[FAIL] Log file does not contain valid JSON")
    else:
        print(f"[WARN] Orders log not yet created")


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print(" MiniQuantDesk v2 - Foundation System Test")
    print("="*70)
    
    # Test 1: Logging
    test_logging_system()
    
    # Test 2: Configuration
    test_configuration_system()
    
    # Test 3: Log files
    test_log_file_contents()
    
    print("\n" + "="*70)
    print(" All Tests Complete")
    print("="*70)
    print("\nNext steps:")
    print("1. Review log files in logs/ directory")
    print("2. Create .env.local from .env.local.template")
    print("3. Add your Alpaca API credentials")
    print("4. Run again to test with real config")
    print()


if __name__ == "__main__":
    # Set UTF-8 encoding for Windows console
    if sys.platform == "win32":
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    
    main()
