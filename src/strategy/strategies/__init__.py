"""Built-in strategies — imported to trigger registration."""

from __future__ import annotations

from src.strategy.registry import register
from src.strategy.strategies.pair_band import PairBandStrategy

register(PairBandStrategy)
