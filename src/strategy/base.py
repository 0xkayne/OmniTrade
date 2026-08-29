"""Strategy abstraction: a strategy consumes bars and emits trade signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class Bar:
    ts: str  # ISO timestamp
    open: float
    high: float
    low: float
    close: float
    context: dict[str, Any] = field(default_factory=dict)  # point-in-time MTF features


@dataclass(frozen=True)
class Signal:
    direction: Literal["buy", "sell"]
    price: float
    trigger: float
    metadata: dict[str, Any] = field(default_factory=dict)  # e.g. buy_price / window_high


class Strategy(ABC):
    """One instance per symbol, holding that symbol's state across bars."""

    name: ClassVar[str]

    @abstractmethod
    def reset(self) -> None:
        """Clear symbol-level state."""

    @abstractmethod
    def on_bar(self, bar: Bar) -> Signal | None:
        """Feed one bar; return a signal or None."""
