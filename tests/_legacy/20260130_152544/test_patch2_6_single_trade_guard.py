from __future__ import annotations

from unittest.mock import Mock

from core.runtime.app import _single_trade_should_block_entry


def test_single_trade_blocks_entry_when_position_exists():
    # Position store has qty != 0
    mock_pos = Mock()
    mock_pos.symbol = "SPY"
    mock_pos.qty = 1

    position_store = Mock()
    position_store.get_position = Mock(return_value=mock_pos)

    exec_engine = Mock()
    exec_engine.get_open_orders = Mock(return_value=[])

    block, has_position, has_open_order = _single_trade_should_block_entry(
        position_store=position_store,
        exec_engine=exec_engine,
        symbol="SPY",
    )

    assert block is True
    assert has_position is True
    assert has_open_order is False


def test_single_trade_blocks_entry_when_open_order_exists():
    position_store = Mock()
    position_store.get_position = Mock(return_value=None)
    position_store.get_all_positions = Mock(return_value=[])

    exec_engine = Mock()
    exec_engine.get_open_orders = Mock(return_value=[{"id": "x"}])

    block, has_position, has_open_order = _single_trade_should_block_entry(
        position_store=position_store,
        exec_engine=exec_engine,
        symbol="SPY",
    )

    assert block is True
    assert has_position is False
    assert has_open_order is True


def test_single_trade_allows_entry_when_clear():
    position_store = Mock()
    position_store.get_position = Mock(return_value=None)
    position_store.get_all_positions = Mock(return_value=[])

    exec_engine = Mock()
    exec_engine.get_open_orders = Mock(return_value=[])

    block, has_position, has_open_order = _single_trade_should_block_entry(
        position_store=position_store,
        exec_engine=exec_engine,
        symbol="SPY",
    )

    assert block is False
    assert has_position is False
    assert has_open_order is False
