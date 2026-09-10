"""Reusable signal algorithms.

Pure functions plus their own algorithm-local state. Nothing here registers
with the strategy registry and nothing here depends on a feature subsystem —
`algos/` adapters and the feature packages both consume from here.

See docs/developer-guide/standards/directory-structure.md §5.5.
"""

from src.strategy.signals.band import BandRule, BandSignal, BandState, evaluate_band

__all__ = ["BandRule", "BandSignal", "BandState", "evaluate_band"]
