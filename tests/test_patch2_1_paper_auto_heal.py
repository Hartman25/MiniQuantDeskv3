from __future__ import annotations

import os
from unittest.mock import MagicMock, Mock, patch
from pathlib import Path

from core.runtime.app import RunOptions, run_app


def test_paper_auto_heal_calls_heal_startup(monkeypatch):
    monkeypatch.setenv("PAPER_AUTO_HEAL", "1")

    mock_config = MagicMock()
    mock_config.broker.api_key = "test_key"
    mock_config.broker.api_secret = "test_secret"
    mock_config.broker.paper_trading = True
    mock_config.strategies = []

    mock_container = MagicMock()
    mock_container.get_config = Mock(return_value=mock_config)

    # reconciler returns discrepancies and supports heal_startup
    mock_reconciler = Mock()
    mock_reconciler.reconcile_startup = Mock(return_value=[MagicMock()])
    mock_reconciler.heal_startup = Mock(return_value=[])

    mock_container.get_reconciler = Mock(return_value=mock_reconciler)

    with patch("core.runtime.app.Container", return_value=mock_container):
        with patch("core.runtime.app.AlpacaBrokerConnector"):
            with patch("core.runtime.app._ensure_strategy_registry_bootstrapped"):
                opts = RunOptions(config_path=Path("config.toml"), mode="paper", run_once=True)
                exit_code = run_app(opts)

    assert exit_code in (0, 1)  # depends on how far runtime goes; not the point here
    mock_reconciler.heal_startup.assert_called_once()


def test_live_mode_never_auto_heals(monkeypatch):
    monkeypatch.setenv("PAPER_AUTO_HEAL", "1")

    mock_config = MagicMock()
    mock_config.broker.api_key = "test_key"
    mock_config.broker.api_secret = "test_secret"
    mock_config.broker.paper_trading = False
    mock_config.strategies = []

    mock_container = MagicMock()
    mock_container.get_config = Mock(return_value=mock_config)

    mock_reconciler = Mock()
    mock_reconciler.reconcile_startup = Mock(return_value=[MagicMock()])
    mock_reconciler.heal_startup = Mock(return_value=[])

    mock_container.get_reconciler = Mock(return_value=mock_reconciler)

    with patch("core.runtime.app.Container", return_value=mock_container):
        with patch("core.runtime.app.AlpacaBrokerConnector"):
            with patch("core.runtime.app._ensure_strategy_registry_bootstrapped"):
                opts = RunOptions(config_path=Path("config.toml"), mode="live", run_once=True)
                exit_code = run_app(opts)

    assert exit_code == 1
    mock_reconciler.heal_startup.assert_not_called()
