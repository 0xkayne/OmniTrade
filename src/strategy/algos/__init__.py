"""Built-in strategies — imported to trigger registration."""

from __future__ import annotations

from src.strategy.algos.pair_band import PairBandStrategy
from src.strategy.registry import register_strategy

register_strategy(PairBandStrategy)
