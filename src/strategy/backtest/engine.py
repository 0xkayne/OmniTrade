"""Backtest signal engine: replay paired-band state machine over historical candles."""

from __future__ import annotations

from datetime import datetime

from src.strategy.price_watch.alerts import BandRule, BandState, evaluate_band


class BacktestEngine:
    """Replay ``evaluate_band`` per symbol over candles, then merge all events by time."""

    def __init__(self, rule: BandRule, window_days: int = 5) -> None:
        self._rule = rule
        self._window_days = window_days

    @staticmethod
    def _epoch(ts: str) -> float:
        return datetime.fromisoformat(ts).timestamp()

    def run_symbol(self, symbol: str, candles: list[dict]) -> list[dict]:
        state = BandState()
        times = [self._epoch(c["ts"]) for c in candles]
        n = len(candles)
        sigs: list[dict] = []
        for i in range(n):
            now = times[i]
            start = i
            cutoff = now - self._window_days * 86400
            while start > 0 and times[start - 1] >= cutoff:
                start -= 1
            if i - start < 2:
                continue
            window_high = max(c["high"] for c in candles[start : i + 1])  # incl current
            sig = evaluate_band(self._rule, state, candles[i]["close"], window_high, now)
            if sig is not None:
                sigs.append(
                    {"ts": candles[i]["ts"], "symbol": symbol, "direction": sig.direction, "price": sig.price}
                )
        return sigs

    def run(self, data: dict[str, list[dict]]) -> list[dict]:
        """Merge all symbols' signals into one event stream sorted by time."""
        events: list[dict] = []
        for symbol, candles in data.items():
            events.extend(self.run_symbol(symbol, candles))
        events.sort(key=lambda e: e["ts"])
        return events
