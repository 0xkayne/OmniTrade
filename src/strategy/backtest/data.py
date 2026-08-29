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

    def __init__(
        self,
        service: CandleService,
        days: int = 30,
        mtf_intervals: tuple[str, ...] = ("1d",),
    ) -> None:
        self._service = service
        self._days = days
        self._mtf_intervals = mtf_intervals

    async def load(self, watchlist: list[WatchItem]) -> dict[str, dict]:
        """Return ``{symbol: {"base": rows, "coarse": {iv: rows}, "derived": {iv: rows}}}``.

        ``coarse`` is each MTF interval read back from the store (deep history); ``derived``
        is the coarse aggregated from the symbol's own base series (incremental, cached in
        the ``derived_candles`` table) covering the base window.
        """
        data: dict[str, dict] = {}
        for item in watchlist:
            base = await self._service.ensure_filled(item, since_days=self._days)
            if base is None or not base.resolvable:
                continue
            venue = base.venue
            coarse: dict[str, list[dict]] = {}
            derived: dict[str, list[dict]] = {}
            for iv in self._mtf_intervals:
                r = await self._service.ensure_filled(item, since_days=self._days, timeframe=iv)
                if r is not None and r.resolvable:
                    coarse[iv] = r.rows
                derived[iv] = await self._service.ensure_derived(item.symbol, venue, iv)
            data[item.symbol] = {"base": base.rows, "coarse": coarse, "derived": derived}
        return data
