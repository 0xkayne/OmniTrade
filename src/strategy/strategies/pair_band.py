"""Pair-band rotation strategy (wraps the existing evaluate_band state machine)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.strategy.base import Bar, Signal, Strategy
from src.strategy.price_watch.alerts import BandRule, BandState, evaluate_band


@dataclass
class PairBandParams:
    buy_drawdown_pct: float = 0.10
    sell_rise_pct: float = 0.15
    window_days: int = 5
    cooldown_hours: float = 6.0


class PairBandStrategy(Strategy):
    """Buy when price falls from the recent window high; sell above the buy price.
    Keeps its own band state + recent bars to compute the window high."""

    name = "pair_band"

    def __init__(
        self,
        buy_drawdown_pct: float = 0.10,
        sell_rise_pct: float = 0.15,
        window_days: int = 5,
        cooldown_hours: float = 6.0,
    ) -> None:
        self.params = PairBandParams(buy_drawdown_pct, sell_rise_pct, window_days, cooldown_hours)
        self._rule = BandRule(buy_drawdown_pct, sell_rise_pct, cooldown_hours * 3600)
        self._state = BandState()
        self._bars: list[Bar] = []

    def reset(self) -> None:
        self._state = BandState()
        self._bars.clear()

    def on_bar(self, bar: Bar) -> Signal | None:
        now = datetime.fromisoformat(bar.ts).timestamp()
        self._bars.append(bar)
        cutoff = now - self.params.window_days * 86400
        while self._bars and datetime.fromisoformat(self._bars[0].ts).timestamp() < cutoff:
            self._bars.pop(0)
        if len(self._bars) < 2:  # need a lookback before judging (matches legacy engine)
            return None
        window_high = max(b.high for b in self._bars)
        sig = evaluate_band(self._rule, self._state, bar.close, window_high, now)
        if sig is None:
            return None
        return Signal(
            direction=sig.direction,
            price=sig.price,
            trigger=sig.trigger,
            metadata={"buy_price": sig.buy_price, "window_high": sig.window_high},
        )
