from pathlib import Path

base = Path(r"C:\Users\Zacha\Desktop\MiniQuantDeskv2")

core_files = [
    ("State", ["core/state/order_machine.py", "core/state/position_store.py", "core/state/transaction_log.py", "core/state/reconciler.py"]),
    ("Events", ["core/events/bus.py", "core/events/types.py", "core/events/handlers.py"]),
    ("Data", ["core/data/contract.py", "core/data/provider.py", "core/data/validator.py", "core/data/pipeline.py", "core/data/cache.py"]),
    ("Risk", ["core/risk/gate.py", "core/risk/sizing.py", "core/risk/limits.py", "core/risk/manager.py"]),
    ("Config", ["core/config/schema.py", "core/config/loader.py"]),
    ("DI", ["core/di/container.py"]),
    ("Brokers", ["core/brokers/alpaca_connector.py"]),
    ("Execution", ["core/execution/engine.py", "core/execution/reconciliation.py"]),
    ("Strategies", ["strategies/base.py", "strategies/registry.py", "strategies/lifecycle.py", "strategies/vwap_mean_reversion.py"]),
    ("Backtest", ["backtest/engine.py", "backtest/data_handler.py", "backtest/simulated_broker.py", "backtest/performance.py"])
]

print("=" * 80)
print("MINIQUANTDESK V2 - CODE INVENTORY")
print("=" * 80)

total_lines = 0
total_files = 0
missing = []

for category, files in core_files:
    print(f"\n{category}:")
    cat_lines = 0
    for f in files:
        fp = base / f
        if fp.exists():
            try:
                lines = len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())
                cat_lines += lines
                total_lines += lines
                total_files += 1
                print(f"  [OK] {f:45} {lines:5} lines")
            except Exception as e:
                print(f"  [!!] {f:45} ERROR")
                missing.append(f)
        else:
            print(f"  [--] {f:45} MISSING")
            missing.append(f)
    print(f"  Subtotal: {cat_lines} lines")

print("\n" + "=" * 80)
print(f"TOTAL: {total_files} files, {total_lines:,} lines")
if missing:
    print(f"\nMISSING: {len(missing)} files")
    for m in missing:
        print(f"  - {m}")
