"""Backtest data loader: fetch historical OHLCV for watchlist symbols."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from src.strategy.price_watch.watchlist import WatchItem

logger = logging.getLogger(__name__)


def _minutes(timeframe: str) -> int:
    m = re.match(r"(\d+)([mhd])", timeframe)
    if not m:
        return 5
    return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class BacktestDataLoader:
    """Fetch historical candles for the watchlist (Hyperliquid → Binance fallback)."""

    def __init__(self, exchanges, registry, days: int = 30, timeframe: str = "5m") -> None:
        self._exchanges = exchanges
        self._registry = registry
        self._days = days
        self._timeframe = timeframe

    async def load(self, watchlist: list[WatchItem]) -> dict[str, list[dict]]:
        """Return {symbol: [{ts, high, low, close}, ...]} ascending; skips failures."""
        data: dict[str, list[dict]] = {}
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=self._days)).timestamp() * 1000)
        per_day = max(1, 1440 // _minutes(self._timeframe))
        limit = per_day * self._days

        for item in watchlist:
            for venue in ("hyperliquid", "binance"):
                if venue not in self._exchanges:
                    continue
                inst = self._registry.find_one(
                    base=item.symbol, venue=venue, market_type=item.market_type,
                    quote_preference=item.quote_preference,
                )
                if inst is None:
                    continue
                ex = self._exchanges[venue]
                try:
                    candles = await ex.fetch_ohlcv(
                        inst.venue_symbol, timeframe=self._timeframe, since=since_ms, limit=limit, params={}
                    )
                except Exception as exc:
                    logger.warning("backtest fetch failed %s/%s: %s", venue, inst.venue_symbol, exc)
                    continue
                if candles:
                    data[item.symbol] = [
                        {"ts": _iso(c[0]), "high": c[2], "low": c[3], "close": c[4]}
                        for c in candles
                    ]
                    break
        return data
