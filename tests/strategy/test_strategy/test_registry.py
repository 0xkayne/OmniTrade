"""Tests for the strategy registry."""

from src.strategy.registry import get_strategy, list_strategies


def test_get_strategy_returns_fresh_instance():
    a = get_strategy("pair_band", buy_drawdown_pct=0.1)
    b = get_strategy("pair_band")
    assert a is not None and b is not None
    assert a is not b  # each call -> new instance (per symbol)


def test_list_strategies_includes_pair_band():
    assert "pair_band" in list_strategies()
