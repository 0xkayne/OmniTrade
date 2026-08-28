"""Tests for the strategy abstraction (Bar / Signal / Strategy)."""

import pytest

from src.strategy.base import Bar, Signal, Strategy


class _NoOnBar(Strategy):
    name = "_no_on_bar"

    def reset(self) -> None:
        pass
    # on_bar NOT implemented -> stays abstract


def test_bar_fields():
    b = Bar(ts="2026-08-27T00:00:00+00:00", open=1.0, high=2.0, low=0.5, close=1.5)
    assert b.close == 1.5


def test_signal_metadata_defaults_empty():
    s = Signal("buy", 1.0, 0.9)
    assert s.metadata == {}


def test_strategy_without_on_bar_cannot_be_instantiated():
    with pytest.raises(TypeError):
        _NoOnBar()
