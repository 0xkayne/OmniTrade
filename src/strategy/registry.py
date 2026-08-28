"""Strategy registry: name -> strategy class, for watch + backtest reuse."""

from __future__ import annotations

from src.strategy.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str, **params) -> Strategy:
    """Return a NEW strategy instance for ``name`` with ``params`` (per symbol)."""
    if name not in _REGISTRY:
        import src.strategy.strategies  # noqa: F401  (triggers registration)

    cls = _REGISTRY[name]
    return cls(**params)


def list_strategies() -> list[str]:
    if not _REGISTRY:
        import src.strategy.strategies  # noqa: F401
    return list(_REGISTRY)
