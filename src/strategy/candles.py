"""Shared OHLCV ingestion + read for the price-watch daemon and the backtester.

Both the live watcher and the backtest replay the *same* persisted candle set
(``watch_candles``). :class:`CandleService` is the single place that resolves a
watchlist item to a venue (Hyperliquid -> Binance), decides how much history to
fetch (incremental gap-fill), upserts into the store, and reads the window back.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.strategy.price_watch.watchlist import WatchItem

logger = logging.getLogger(__name__)

DEFAULT_VENUES = ["hyperliquid", "binance"]


@dataclass
class FillResult:
    """Outcome of one :meth:`CandleService.ensure_filled` call for one watchlist item.

    ``resolvable=False`` means no configured venue holds the symbol — a permanent
    condition the caller may cache as unresolvable. A ``None`` return from
    ``ensure_filled`` means the symbol resolved but fetching failed / returned
    nothing — a transient condition the caller may retry next cycle.
    """

    venue: str
    rows: list[dict]
    resolvable: bool = True


class CandleService:
    """Resolve venue, gap-fill candles into the store, read the requested window."""

    def __init__(self, exchanges: dict, registry, store, timeframe: str = "5m") -> None:
        self._exchanges = exchanges
        self._registry = registry
        self._store = store
        self._timeframe = timeframe

    async def ensure_filled(self, item: WatchItem, *, since_days: int) -> FillResult | None:
        """Return :class:`FillResult` covering at least ``since_days`` back.

        Resolves the instrument venue in Hyperliquid -> Binance order (falling
        through on a fetch failure so an asset unreachable on one venue is read
        from the next), fetches only the missing tail, upserts every candle, then
        reads the requested window back from the store.
        """
        cutoff_ms = int(self._now_ms()) - since_days * 86400_000
        cutoff_iso = self._iso(cutoff_ms)

        for venue in DEFAULT_VENUES:
            if venue not in self._exchanges:
                continue
            inst = self._registry.find_one(
                base=item.symbol,
                venue=venue,
                market_type=item.market_type,
                quote_preference=item.quote_preference,
            )
            if inst is None:
                continue  # not on this venue -> try the next
            exchange = self._exchanges[venue]
            fetch_since_ms = await self._fetch_since_ms(item.symbol, venue, cutoff_ms)
            try:
                candles = await exchange.fetch_ohlcv(
                    inst.venue_symbol,
                    timeframe=self._timeframe,
                    since=fetch_since_ms,
                    limit=self._candles_per_day(self._timeframe) * since_days,
                    params={},
                )
            except Exception as exc:
                logger.warning("fetch_ohlcv failed for %s/%s: %s", venue, inst.venue_symbol, exc)
                continue  # fall through to the next venue
            if not candles:
                logger.warning("no candles for %s on %s", item.symbol, venue)
                continue
            for c in candles:
                await self._store.upsert_watch_candle(item.symbol, venue, self._iso(c[0]), c[1], c[2], c[3], c[4])
            rows = await self._store.get_watch_candles(item.symbol, venue, cutoff_iso)
            return FillResult(venue=venue, rows=rows)

        # No configured venue held the instrument.
        return FillResult(venue="", rows=[], resolvable=False)

    async def _fetch_since_ms(self, asset: str, venue: str, cutoff_ms: int) -> int:
        """Decide how far back to fetch given what the store already holds.

        Returns integer epoch-milliseconds (exchanges reject float ``startTime``).
        """
        latest = await self._store.get_latest_watch_candle(asset, venue)
        if latest is None:
            return int(cutoff_ms)  # no data at all -> fill the full window
        earliest = await self._store.get_earliest_watch_candle(asset, venue)
        earliest_ms = self._bar_ts_ms(earliest)
        latest_ms = self._bar_ts_ms(latest)
        if earliest_ms > cutoff_ms:
            return int(cutoff_ms)  # past gap: asked for more history than accumulated
        if latest_ms <= cutoff_ms:
            return int(cutoff_ms)  # all stored data is stale -> refresh the window
        return int(latest_ms)  # past covered + recent exists -> fetch only the tail

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _candles_per_day(timeframe: str) -> int:
        m = re.match(r"^(\d+)([mhdw])$", timeframe)
        if not m:
            return 288  # default: 5m
        minutes = int(m.group(1)) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[m.group(2)]
        return max(1, 1440 // minutes)

    @staticmethod
    def _now_ms() -> float:
        return datetime.now(timezone.utc).timestamp() * 1000

    @staticmethod
    def _iso(ms: float) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

    @staticmethod
    def _bar_ts_ms(row: dict) -> int:
        raw = row.get("ts")
        if isinstance(raw, (int, float)):
            return int(raw)
        return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
