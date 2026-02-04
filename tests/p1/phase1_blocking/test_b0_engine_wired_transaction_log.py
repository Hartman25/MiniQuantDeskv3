"""
Phase 1 Blocking - Patch 1
INVARIANT:
Container MUST wire TransactionLog into OrderExecutionEngine so that
restart-safe idempotency is ACTIVE in the real runtime wiring.

This test fails if Container builds the engine without transaction_log.
"""

from decimal import Decimal

from core.di.container import Container
from tests.fixtures.runtime_harness import StubBrokerConnector


def test_container_wires_transaction_log_into_execution_engine(tmp_path, monkeypatch):
    # Ensure file paths resolve into temp dir so we don't touch repo data/
    # We do this by forcing process CWD to tmp_path for the duration.
    monkeypatch.chdir(tmp_path)

    # Use repo config directory structure expectations: config/config.yaml
    # Copy minimal config into tmp_path/config/config.yaml by importing the real one.
    # Simpler: just read the real config file contents and write it here.
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[3]  # tests/p1/phase1_blocking/ -> repo root
    src_cfg = repo_root / "config" / "config.yaml"
    assert src_cfg.exists(), "Expected config/config.yaml to exist in repo for container init"

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(src_cfg.read_text(), encoding="utf-8")

    c = Container()
    c.initialize(str(tmp_path / "config" / "config.yaml"))

    # Append a prior ORDER_SUBMIT event to the persistent transaction log
    tx = c.get_transaction_log()
    tx.append({"event_type": "ORDER_SUBMIT", "internal_order_id": "ORD-seed-1"})

    # Now build engine via real runtime wiring boundary
    broker = StubBrokerConnector(initial_equity=Decimal("10000.0"))
    c.set_broker_connector(broker)

    engine = c.get_order_execution_engine()
    assert engine is not None, "Execution engine should be initialized after set_broker_connector()"

    # The only way this passes is if Container passed transaction_log into engine
    assert "ORD-seed-1" in engine._submitted_order_ids, (
        "Container did not wire transaction_log into OrderExecutionEngine; "
        "restart-safe idempotency is NOT enforced in production wiring."
    )
