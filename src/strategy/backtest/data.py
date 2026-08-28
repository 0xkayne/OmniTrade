"""Backtest data loader: read shared persisted OHLCV for the watchlist symbols."""

from __future__ import annotations

from src.strategy.candles import CandleService
from src.strategy.price_watch.watchlist import WatchItem


class BacktestDataLoader:
    """Read candles for the watchlist from the shared :class:`CandleService` store.

    Reuses the same persisted ``watch_candles`` set the live watcher writes, so a
    backtest replays the exact data the watcher has accumulated (fetching the same
    way: Hyperliquid -> Binance, incremental gap-fill).
    """

    def __init__(self, service: CandleService, days: int = 30) -> None:
        self._service = service
        self._days = days

    async def load(self, watchlist: list[WatchItem]) -> dict[str, list[dict]]:
        """Return {symbol: rows} ascending; skips unresolved / transient failures."""
        data: dict[str, list[dict]] = {}
        for item in watchlist:
            result = await self._service.ensure_filled(item, since_days=self._days)
            if result is None or not result.resolvable:
                continue
            data[item.symbol] = result.rows
        return data
