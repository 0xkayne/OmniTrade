"""WebSocket-streamed orderbook cache for zero-latency quote access.

Creates independent ccxt.pro exchange instances (one per venue per market
type) with fixed defaultType, completely decoupled from the main REST
exchange instances used for order placement.

The main Orchestrator keeps using ccxt.async_support for REST operations;
the cache runs its own ccxt.pro instances for WS market data only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ccxt.base.errors import BadSymbol
from ccxt.base.errors import NotSupported as CCXTNotSupported

if TYPE_CHECKING:
    from .instrument import Instrument
    from .quote import Quote

logger = logging.getLogger(__name__)

# Risk mitigation thresholds
MAX_STALENESS_MS = 500
MAX_SILENCE_SEC = 5.0


@dataclass
class _CacheEntry:
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    ts: float
    exchange_ts: float | None = None


CacheKey = tuple[str, str, str]


class OrderbookCache:
    """In-memory orderbook cache fed by per-venue WebSocket streams.

    Creates its own ccxt.pro exchange instances (spot + swap per venue)
    so defaultType is permanently fixed per instance — no race condition.
    """

    def __init__(
        self,
        venue_configs: list[dict],
        max_staleness_ms: int = MAX_STALENESS_MS,
        max_silence_sec: float = MAX_SILENCE_SEC,
    ):
        self._max_staleness_ms = max_staleness_ms
        self._max_silence_sec = max_silence_sec

        # f"{venue}_{market_type}" → ccxt.pro exchange
        self._ws_exchanges: dict[str, object] = _create_ws_exchanges(venue_configs)

        # (venue, ws_market_type, venue_symbol) → _CacheEntry
        self._cache: dict[CacheKey, _CacheEntry] = {}

        # f"{venue}_{market_type}" → asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}

        # f"{venue}_{market_type}" keys flagged as unhealthy
        self._stale_keys: set[str] = set()

    # -- public API ----------------------------------------------------

    async def start(self, instruments_by_venue: dict[str, list[Instrument]]) -> None:
        """Launch one streaming task per venue+market_type combination."""
        # Load markets for all WS exchange instances concurrently
        await asyncio.gather(
            *(ex.load_markets() for ex in self._ws_exchanges.values()),
            return_exceptions=True,
        )

        for venue, instruments in instruments_by_venue.items():
            for mt in {"spot", "swap"}:
                key = f"{venue}_{mt}"
                ws_ex = self._ws_exchanges.get(key)
                if ws_ex is None:
                    continue
                mt_instruments = _select_instruments(instruments, mt)
                if not mt_instruments:
                    continue
                self._tasks[key] = asyncio.create_task(
                    self._stream_venue(key, ws_ex, mt_instruments),
                    name=f"ob-cache-{key}",
                )

        # Warmup: wait for at least one snapshot per task that hasn't exited
        deadline = time.perf_counter() + 60.0
        while time.perf_counter() < deadline:
            alive_tasks = {k: t for k, t in self._tasks.items() if not t.done()}
            if not alive_tasks:
                logger.warning("Orderbook cache: all WS tasks exited, using REST fallback")
                break
            all_warm = all(
                any(
                    _cache_key_for_stream(key, sym) in self._cache
                    for sym in _get_symbols_for_key(key, instruments_by_venue)
                )
                for key in alive_tasks
            )
            if all_warm:
                break
            await asyncio.sleep(0.5)
        else:
            logger.warning("Orderbook cache warmup timed out — using REST fallback")

    def get_quote(self, instrument: Instrument) -> Quote | None:
        from .quote import Quote

        key = f"{instrument.venue}_{_to_ws_market_type(instrument.market_type)}"
        if key in self._stale_keys:
            return None

        entry = self._cache.get(
            _cache_key(
                instrument.venue,
                _to_ws_market_type(instrument.market_type),
                instrument.venue_symbol,
            )
        )
        if entry is None:
            logger.debug(
                "cache miss for %s:%s (have: %s)",
                instrument.venue,
                instrument.venue_symbol,
                list(self._cache.keys())[:5],
            )
            return None

        now = time.perf_counter()
        if (now - entry.ts) * 1000.0 > self._max_staleness_ms:
            return None

        if not entry.bids or not entry.asks:
            return None

        bid_price, bid_size = entry.bids[0]
        ask_price, ask_size = entry.asks[0]
        if bid_price <= 0 or ask_price <= 0:
            return None

        return Quote(
            instrument=instrument,
            fetched_at=time.time(),
            bid_price=bid_price, bid_size=bid_size,
            ask_price=ask_price, ask_size=ask_size,
            mid_price=(bid_price + ask_price) / 2.0,
            taker_fee_rate=instrument.taker_fee_rate,
            maker_fee_rate=instrument.maker_fee_rate,
            _bids=list(entry.bids),
            _asks=list(entry.asks),
        )

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for ex in self._ws_exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass
        self._cache.clear()
        self._stale_keys.clear()

    # -- streaming ------------------------------------------------------

    async def _stream_venue(self, key: str, exchange, instruments: list[Instrument]) -> None:
        reconnect_delay = 1.0
        while True:
            for inst in instruments:
                sym = inst.venue_symbol
                try:
                    ob = await asyncio.wait_for(
                        exchange.watch_order_book(sym),
                        timeout=self._max_silence_sec,
                    )
                    self._apply_update(key, sym, ob)
                    self._stale_keys.discard(key)
                    reconnect_delay = 1.0
                except (CCXTNotSupported, BadSymbol):
                    logger.warning("%s: watch_orderbook not supported for this instrument", key)
                    self._stale_keys.add(key)
                    return
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                except Exception:
                    logger.warning("%s: WS error, reconnecting in %.1fs", key, reconnect_delay, exc_info=True)
                    self._stale_keys.add(key)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    break

    def _apply_update(self, key: str, sym: str, ob: dict) -> None:
        if not sym:
            return
        bids_raw = ob.get("bids", [])
        asks_raw = ob.get("asks", [])
        if not bids_raw or not asks_raw:
            return
        bids = _parse_side(bids_raw)
        asks = _parse_side(asks_raw)
        if not bids or not asks or bids[0][0] <= 0 or asks[0][0] <= 0:
            return
        if bids[0][0] >= asks[0][0]:
            return
        self._cache[_cache_key_for_stream(key, sym)] = _CacheEntry(
            bids=bids, asks=asks,
            ts=time.perf_counter(),
            exchange_ts=ob.get("timestamp"),
        )


# -- helpers -----------------------------------------------------------


def _create_ws_exchanges(venue_configs: list[dict]) -> dict[str, object]:
    """Create ccxt.pro exchange instances — one per venue per market type.

    Each instance has a fixed defaultType so watch_order_book's internal
    fetch_order_book_snapshot always resolves the correct market.
    """
    import ccxt.pro as ccxt_pro

    result: dict[str, object] = {}
    for v in venue_configs:
        for mt in ("spot", "swap"):
            key = f"{v['name']}_{mt}"
            ex = getattr(ccxt_pro, v["name"])({
                "enableRateLimit": True,
                "options": {"defaultType": mt},
            })
            # Binance testnet: enable demo trading + swap WS URL
            if v["name"] == "binance" and v.get("network") == "testnet":
                try:
                    ex.enable_demo_trading(True)
                except Exception:
                    pass
                demo_ws = ex.urls.get("demo", {}).get("ws")
                if demo_ws:
                    ex.urls["api"]["ws"] = demo_ws
            result[key] = ex
    return result


def _to_ws_market_type(market_type: str) -> str:
    """Map OmniTrade market_type to ccxt defaultType value."""
    return "swap" if market_type == "perp" else market_type


def _cache_key(venue: str, market_type: str, venue_symbol: str) -> CacheKey:
    return (venue, market_type, venue_symbol)


def _cache_key_for_stream(key: str, venue_symbol: str) -> CacheKey:
    venue, mt = key.rsplit("_", 1)
    return _cache_key(venue, mt, venue_symbol)


def _get_symbols_for_key(key: str, instruments_by_venue: dict[str, list[Instrument]]) -> list[str]:
    venue, mt = key.rsplit("_", 1)
    instruments = instruments_by_venue.get(venue, [])
    return [i.venue_symbol for i in instruments if _to_ws_market_type(i.market_type) == mt]


# Pairs to stream via WebSocket. Only these get real-time cached orderbooks;
# everything else falls back to REST.  Extend this set if you trade other assets.
_DEFAULT_PRIORITY_BASES = {"BTC", "ETH"}
_DEFAULT_PRIORITY_QUOTES = {"USDT", "USDC", "USD"}


def _select_instruments(
    instruments: list[Instrument],
    mt: str,
    priority_bases: set[str] | None = None,
    priority_quotes: set[str] | None = None,
) -> list[Instrument]:
    """Filter to priority pairs, sorted by base then quote."""
    bases = priority_bases or _DEFAULT_PRIORITY_BASES
    quotes = priority_quotes or _DEFAULT_PRIORITY_QUOTES
    mt_instruments = [i for i in instruments if _to_ws_market_type(i.market_type) == mt]
    priority = [i for i in mt_instruments if i.base.symbol in bases and i.quote.symbol in quotes]
    priority.sort(key=lambda i: (
        0 if i.base.symbol == "BTC" else 1,
        {"USDT": 0, "USDC": 1, "USD": 2}.get(i.quote.symbol, 9),
    ))
    dropped = len(mt_instruments) - len(priority)
    if dropped > 0:
        logger.info("WS cache: streaming %d/%d %s pairs (%d dropped, will use REST)",
                    len(priority), len(mt_instruments), mt, dropped)
    return priority


def _parse_side(raw_side: list) -> list[tuple[float, float]]:
    result = []
    for entry in raw_side:
        if isinstance(entry, (list, tuple)):
            result.append((float(entry[0]), float(entry[1])))
    return result
