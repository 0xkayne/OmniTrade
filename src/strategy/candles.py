"""Shared OHLCV ingestion + read for the price-watch daemon and the backtester.

Both the live watcher and the backtest replay the *same* persisted candle set
(``watch_candles``). :class:`CandleService` is the single place that resolves a
watchlist item to a venue (Hyperliquid -> Binance), decides how much history to
fetch (incremental gap-fill), upserts into the store, and reads the window back.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import ccxt

if TYPE_CHECKING:
    from src.strategy.price_watch.watchlist import WatchItem

logger = logging.getLogger(__name__)

DEFAULT_VENUES = ["hyperliquid", "binance"]

# Transient fetch errors worth retrying with backoff (HTTP 429 / 5xx / network timeout).
_RETRYABLE = (ccxt.RateLimitExceeded, ccxt.DDoSProtection, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout)

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
            now_ms = int(self._now_ms())
            # ``effective_days`` is the SEED horizon — how deep we fetch. The caller's
            # ``since_days`` is the WINDOW — how far back we read and hand to the strategy.
            # A deep seed must fill the store without widening the alert window on first
            # contact, so the two cutoffs diverge and the read-back stays ``since_days``.
            fetch_cutoff_ms = now_ms - effective_days * 86400_000
            read_cutoff_iso = self._iso(now_ms - since_days * 86400_000)
            fetch_since_ms = await self._fetch_since_ms(
                item.symbol, venue, fetch_cutoff_ms, effective_days, latest=latest, interval=tf
            )
            try:
                candles = await self._fetch_ohlcv(
                    exchange, venue, inst.venue_symbol, tf, fetch_since_ms, self._fetch_limit(effective_days, tf)
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
            rows = await self._store.get_watch_candles(item.symbol, venue, read_cutoff_iso, tf)
            return FillResult(venue=venue, rows=rows)

        # No configured venue held the instrument.
        return FillResult(venue="", rows=[], resolvable=False)

    async def ensure_derived(self, asset: str, venue: str, interval: str) -> list[dict]:
        """Aggregate the store's base series (``self._timeframe``) into a persisted coarse cache.

        Used for MTF context so the 5m->coarse aggregation is computed once and only
        delta-filled thereafter, instead of re-aggregating on every run.
        """
        from src.strategy import mtf

        return await mtf.ensure_derived(self._store, asset, venue, self._timeframe, interval)

    def _fetch_limit(self, effective_days: int, timeframe: str) -> int:
        """Candles to request for the whole requested window (a total, not per-call)."""
        return self._candles_per_day(timeframe) * effective_days

    @staticmethod
    def _interval_ms(timeframe: str) -> int:
        """Milliseconds per bar for an interval like ``5m``/``1h``/``1d``."""
        m = re.match(r"^(\d+)([mhdw])$", timeframe)
        if not m:
            return 300_000
        return int(m.group(1)) * {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[m.group(2)]

    async def _fetch_ohlcv(
        self, exchange, venue: str, symbol: str, timeframe: str, since_ms: int, window_candles: int
    ) -> list:
        """Fetch candles for one venue.

        Binance caps a single klines response at 1000 and ccxt's ``paginate`` mode is
        itself bounded at ~10000, so we page Binance forward in 1000-candle chunks to
        reach the full requested window (up to the seed horizon). Every other venue —
        Hyperliquid clamps its retrievable window and ignores ``since`` beyond it — is
        taken in a single call, since paging it would just spin on the same clamp.
        """
        if venue == "binance":
            return await self._fetch_paged(exchange, symbol, timeframe, since_ms, window_candles)
        return await self._fetch_with_retry(exchange, symbol, timeframe, since_ms, window_candles)

    async def _fetch_paged(self, exchange, symbol: str, timeframe: str, since_ms: int, window_candles: int) -> list:
        """Forward-page a venue that honors ``since`` into ``window_candles`` (or ``now``).

        Each request takes at most 1000 candles (a venue's per-request cap); ``since``
        advances past the last candle returned so consecutive pages are contiguous. Stops
        when the window is full, we reach the present bar, or the venue returns nothing
        (the symbol's listing or an illiquid gap).
        """
        page = 1000
        interval_ms = self._interval_ms(timeframe)
        rows: list = []
        since = int(since_ms)
        while len(rows) < window_candles:
            chunk = await self._fetch_with_retry(exchange, symbol, timeframe, since, page)
            if not chunk:
                break
            rows.extend(chunk)
            last = chunk[-1][0]
            if last >= int(self._now_ms()) - interval_ms:
                break  # reached the present bar
            since = int(last) + interval_ms
        return rows

    @staticmethod
    async def _fetch_with_retry(
        exchange, symbol: str, timeframe: str, since: int, limit: int, max_retries: int = 4
    ) -> list:
        """One ``fetch_ohlcv`` call, retrying transient rate-limit/network errors.

        A single page hitting a 429/5xx/timeout used to abort the whole symbol's backfill
        (and the live window only tops up, never deepens), so a rate-limited symbol stayed
        shallow until the next restart. On a retryable error we sleep the exchange's
        ``Retry-After`` when present, else an exponential backoff (1s→2s→4s, capped 30s),
        up to ``max_retries``; after that the error is re-raised for the caller to handle.
        """
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit, params={})
            except _RETRYABLE as exc:
                last_err = exc
                retry_after = getattr(exc, "retry_after", None)
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "fetch_ohlcv rate-limited for %s %s, retrying in %.1fs (attempt %d/%d)",
                    symbol, timeframe, wait, attempt + 1, max_retries + 1,
                )
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30.0)
        raise last_err  # type: ignore[misc]

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
