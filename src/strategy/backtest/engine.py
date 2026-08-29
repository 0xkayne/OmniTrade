"""Backtest signal engine: drive a strategy over historical candles and merge events."""

from __future__ import annotations

from collections.abc import Callable

from src.strategy.backtest.mtf import coarse_trend, hybrid_coarse
from src.strategy.base import Bar, Strategy


class BacktestEngine:
    """Feed bars into a per-symbol strategy instance, then merge events by time.

    Execution is no-lookahead: a signal produced at bar ``i`` (on its close) fills at
    bar ``i+1``'s open plus slippage; a signal on the last bar has no next bar and is
    dropped. Each symbol's ``bundle`` carries ``{"base": rows, "coarse": {interval: rows}}``
    so the strategy can reference coarse-TF context (see :mod:`src.strategy.backtest.mtf`).
    """

    def __init__(
        self,
        strategy_factory: Callable[[], Strategy],
        slippage_pct: float = 0.001,
        mtf_intervals: tuple[str, ...] = ("1d",),
        mtf_sma: int = 10,
    ) -> None:
        self._factory = strategy_factory
        self._slippage_pct = slippage_pct
        self._mtf_intervals = mtf_intervals
        self._mtf_sma = mtf_sma

    def run(self, data: dict[str, dict]) -> list[dict]:
        """Merge all symbols' signals into one event stream sorted by time."""
        events: list[dict] = []
        for symbol, bundle in data.items():
            events.extend(self.run_symbol(symbol, bundle))
        events.sort(key=lambda e: e["ts"])
        return events

    def run_symbol(self, symbol: str, bundle: dict) -> list[dict]:
        base = bundle["base"]
        coarse_map = bundle.get("coarse", {})
        strat = self._factory()
        contexts = self._build_contexts(base, coarse_map)
        sigs: list[dict] = []
        for i, c in enumerate(base):
            bar = Bar(
                ts=c["ts"],
                open=float(c.get("open") or c["close"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                context=contexts[i] if i < len(contexts) else {},
            )
            sig = strat.on_bar(bar)
            if sig is None or i + 1 >= len(base):
                continue  # no signal, or last bar (cannot execute without lookahead)
            nxt = base[i + 1]
            fill = self._fill_price(nxt.get("open") or nxt["close"], sig.direction)
            sigs.append({"ts": nxt["ts"], "symbol": symbol, "direction": sig.direction, "price": fill})
        return sigs

    def _fill_price(self, open_px, direction: str) -> float:
        factor = (1 + self._slippage_pct) if direction == "buy" else (1 - self._slippage_pct)
        return float(open_px) * factor

    def _build_contexts(self, base: list[dict], coarse_map: dict[str, list[dict]]) -> list[dict]:
        """Per-bar ``coarse_trend`` from the primary MTF interval (first configured)."""
        contexts: list[dict] = [{} for _ in range(len(base))]
        if not self._mtf_intervals:
            return contexts
        primary = self._mtf_intervals[0]
        store_rows = coarse_map.get(primary, [])
        hybrid = hybrid_coarse(base, store_rows, primary)
        for i, c in enumerate(base):
            contexts[i]["coarse_trend"] = coarse_trend(hybrid, c["ts"], primary, self._mtf_sma)
        return contexts
