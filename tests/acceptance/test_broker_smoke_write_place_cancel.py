import os
import time
import logging
import pytest

log = logging.getLogger(__name__)

pytestmark = pytest.mark.acceptance


def _env(name: str) -> str | None:
    v = os.getenv(name)
    return v.strip() if isinstance(v, str) else v


def _have_broker_creds() -> bool:
    return bool(
        (_env("BROKER_API_KEY") and _env("BROKER_API_SECRET"))
        or (_env("ALPACA_API_KEY") and _env("ALPACA_API_SECRET"))
        or (_env("APCA_API_KEY_ID") and _env("APCA_API_SECRET_KEY"))
    )


@pytest.mark.skipif(not _have_broker_creds(), reason="Missing broker creds in env (BROKER_* / ALPACA_* / APCA_*).")
def test_phase1_runtime_run_once_smoke_verbose(tmp_path):
    """
    Runs real runtime once in PAPER mode (no signals).
    Verbose logging prints each step and timing to pinpoint where it gets stuck.
    """
    log.info("=== PHASE1 RUNTIME SMOKE START ===")
    os.environ["PAPER_TRADING"] = "true"
    os.environ.pop("MQD_ENABLE_ASYNC_RISK_GATE", None)

    log.info("Env PAPER_TRADING=%s", _env("PAPER_TRADING"))
    log.info("Env MQD_ENABLE_ASYNC_RISK_GATE=%s", _env("MQD_ENABLE_ASYNC_RISK_GATE"))

    log.info("[1/4] Importing core.runtime.app ...")
    t0 = time.time()
    import core.runtime.app as app_mod
    log.info("Imported app in %.2fs", time.time() - t0)

    from pathlib import Path
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "config_micro.yaml"
    if not cfg_path.exists():
        pytest.skip(f"Config file not found: {cfg_path}")
    log.info("[2/4] Using config path: %s", cfg_path)

    opts = app_mod.RunOptions(
        config_path=cfg_path,
        mode="paper",
        run_interval_s=0,
        run_once=True,
    )
    log.info("[3/4] Calling app.run(run_once=True) ...")
    t1 = time.time()
    rc = app_mod.run(opts)
    log.info("app.run returned rc=%s in %.2fs", rc, time.time() - t1)

    log.info("[4/4] Assert rc == 0")
    assert rc == 0, f"Runtime smoke failed (rc={rc})"

    log.info("=== PHASE1 RUNTIME SMOKE PASS ===")
