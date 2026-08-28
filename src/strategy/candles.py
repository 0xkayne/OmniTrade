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

# A front-of-window hole up to this size is treated as covered. Hyperliquid serves
# only ~18 days of 5m history and ignores ``since``, so a refetch can't extend the
# window anyway — we avoid re-downloading the whole window for a sub-day hole.
_FETCH_TOLERANCE_MS = 86400_000


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

    async def ensure_filled(
        self,
        item: WatchItem,
        *,
        since_days: int,
        seed_days: int | None = None,
        timeframe: str | None = None,
    ) -> FillResult | None:
        """Return :class:`FillResult` covering at least ``since_days`` back.

        Resolves the instrument venue in Hyperliquid -> Binance order (falling
        through on a fetch failure so an asset unreachable on one venue is read
        from the next), fetches only the missing tail, upserts every candle, then
        reads the requested window back from the store.

        ``seed_days`` is a one-time history horizon used only on first contact —
        if the store has never held a candle for this asset/venue we reach back
        that far so the accumulated series grows as deep as the exchange serves
        (Hyperliquid clamps its 5m window; Binance paginates to the full horizon).
        Once a candle exists we fall back to ``since_days`` and just top up the
        tail, so a restart never re-downloads the whole deep history.

        ``timeframe`` overrides the service's default interval. Each interval is its
        own clean series (never mixed): it is passed to the store reads/writes as the
        row's ``interval``, so 5m / 1h / 4h / 1d never collide.
        """
        tf = timeframe or self._timeframe
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
            latest = await self._store.get_latest_watch_candle(item.symbol, venue, tf)
            effective_days = seed_days if (seed_days is not None and seed_days > 0 and latest is None) else since_days
            cutoff_ms = int(self._now_ms()) - effective_days * 86400_000
            cutoff_iso = self._iso(cutoff_ms)
            fetch_since_ms = await self._fetch_since_ms(
                item.symbol, venue, cutoff_ms, effective_days, latest=latest, interval=tf
            )
            try:
                candles = await exchange.fetch_ohlcv(
                    inst.venue_symbol,
                    timeframe=tf,
                    since=fetch_since_ms,
                    limit=self._fetch_limit(effective_days, tf),
                    params=self._fetch_params(venue),
                )
            except Exception as exc:
                logger.warning("fetch_ohlcv failed for %s/%s: %s", venue, inst.venue_symbol, exc)
                continue  # fall through to the next venue
            if not candles:
                logger.warning("no candles for %s on %s", item.symbol, venue)
                continue
            # One transaction per symbol (a full window is ~thousands of candles).
            await self._store.upsert_watch_candles(
                [(item.symbol, venue, self._iso(c[0]), c[1], c[2], c[3], c[4], tf) for c in candles]
            )
            rows = await self._store.get_watch_candles(item.symbol, venue, cutoff_iso, tf)
            return FillResult(venue=venue, rows=rows)

        # No configured venue held the instrument.
        return FillResult(venue="", rows=[], resolvable=False)

    def _fetch_limit(self, effective_days: int, timeframe: str) -> int:
        """Candles to request for the whole requested window.

        ``limit`` here is a *total* cap, not a per-call page size: Binance caps each
        klines response at 1000, but with ``paginate`` ccxt keeps requesting until it
        has ``limit`` candles **or reaches the present**, so we pass the full window
        even for Binance and let it page to the end. Every other venue takes the whole
        window in a single call.
        """
        return self._candles_per_day(timeframe) * effective_days

    @staticmethod
    def _fetch_params(venue: str) -> dict:
        """Pagination flag. Only Binance supports (and benefits from) it — Hyperliquid
        clamps its retrievable 5m window, so paging it only spins on empty pages."""
        return {"paginate": True} if venue == "binance" else {}

    async def _fetch_since_ms(
        self,
        asset: str,
        venue: str,
        cutoff_ms: int,
        since_days: int,
        latest: dict | None = None,
        interval: str = "5m",
    ) -> int:
        """Decide how far back to fetch given what the store already holds.

        Returns integer epoch-milliseconds (exchanges reject float ``startTime``).
        Backfills the front of the window only when the store clearly has less
        history than requested (``span < since_days - tolerance``); a sub-day hole
        is ignored so we don't re-download the whole window on every run. When the
        store's newest candle is older than the window (the process was down), we
        fill from that candle so the restart gap is closed rather than left as a
        permanent hole in the accumulated series.
        """
        if latest is None:
            latest = await self._store.get_latest_watch_candle(asset, venue, interval)
        if latest is None:
            return int(cutoff_ms)  # no data at all -> fill the full window
        earliest = await self._store.get_earliest_watch_candle(asset, venue, interval)
        earliest_ms = self._bar_ts_ms(earliest)
        latest_ms = self._bar_ts_ms(latest)
        if latest_ms <= cutoff_ms:
            return int(latest_ms)  # all stored data is stale -> close the gap back to it
        if earliest_ms <= cutoff_ms:
            return int(latest_ms)  # front of window covered -> fetch only the tail
        # Front not covered. Backfill only if the store is visibly shorter than the
        # requested window; otherwise the hole is just the exchange's history clamp.
        span_ms = latest_ms - earliest_ms
        window_ms = since_days * 86400_000
        if span_ms < window_ms - _FETCH_TOLERANCE_MS:
            return int(cutoff_ms)  # backfill the missing older history
        return int(latest_ms)  # effectively covered -> fetch only the tail

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
