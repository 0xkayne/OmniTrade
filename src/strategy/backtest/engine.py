"""Backtest signal engine: drive a strategy over historical candles and merge events."""

from __future__ import annotations

from collections.abc import Callable

from src.strategy.base import Bar, Strategy


class BacktestEngine:
    """Feed bars into a per-symbol strategy instance, then merge events by time."""

    def __init__(self, strategy_factory: Callable[[], Strategy]) -> None:
        self._factory = strategy_factory

    def run_symbol(self, symbol: str, candles: list[dict]) -> list[dict]:
        strat = self._factory()
        sigs: list[dict] = []
        for c in candles:
            bar = Bar(
                ts=c["ts"],
                open=float(c.get("open") or c["close"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
            )
            sig = strat.on_bar(bar)
            if sig is not None:
                sigs.append({"ts": c["ts"], "symbol": symbol, "direction": sig.direction, "price": sig.price})
        return sigs

    def run(self, data: dict[str, list[dict]]) -> list[dict]:
        """Merge all symbols' signals into one event stream sorted by time."""
        events: list[dict] = []
        for symbol, candles in data.items():
            events.extend(self.run_symbol(symbol, candles))
        events.sort(key=lambda e: e["ts"])
        return events
