"""Integration tests for PriceWatcher: fetch → persist → evaluate → deliver (mocked)."""

import time
from pathlib import Path

from src.core.base_exchange import NetworkType
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange
from src.market.registry import InstrumentRegistry
from src.persistence.store import PersistenceStore
from src.strategy.price_watch.watcher import PriceWatchConfig, PriceWatcher
from src.strategy.price_watch.watchlist import WatchItem

DAY_MS = 86400_000


def _make_perp(venue, base_symbol, venue_symbol):
    return Instrument(
        venue=venue,
        network=NetworkType.TESTNET,
        market_type="perp",
        base=Asset(base_symbol),
        quote=Asset("USDT"),
        venue_symbol=venue_symbol,
        min_qty=0.001,
        qty_step=0.001,
        price_step=0.1,
        taker_fee_rate=0.0005,
        maker_fee_rate=0.0002,
    )


class _FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return True


async def _make_env(tmp_path, candles_hl, base_symbol="SOL", venue_symbol="SOL/USDT:USDT", only_on="hyperliquid"):
    hl = MockExchange("hyperliquid")
    bn = MockExchange("binance")
    hl.set_ohlcv(venue_symbol, candles_hl)
    registry = InstrumentRegistry()
    registry.add(_make_perp("hyperliquid", base_symbol, venue_symbol))
    if only_on != "hyperliquid":
        registry.add(_make_perp("binance", base_symbol, base_symbol + "USDT"))
        bn.set_ohlcv(base_symbol + "USDT", candles_hl)
    exchanges = {"hyperliquid": hl, "binance": bn}
    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    telegram = _FakeTelegram()
    return exchanges, registry, store, telegram


def _candles_with_break(now_ms):
    """Prior lows around 100-105; the latest candle collapses to ~85 (deep break)."""
    return [
        [now_ms - 4 * DAY_MS, 110, 100, 120, 110, 1],  # within the 5-day window
        [now_ms - 3 * DAY_MS, 120, 105, 130, 120, 1],
        [now_ms - DAY_MS, 100, 99, 125, 100, 1],
        [now_ms, 95, 85, 95, 85, 1],
    ]


async def test_tick_fetches_persists_and_alerts(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    item = WatchItem(symbol="SOL", tag="公链")
    watcher = PriceWatcher(
        exchanges, registry, store, [item], telegram,
        PriceWatchConfig(interval_seconds=600, dry_run=False),
    )
    await watcher.tick()

    rows = await store.get_watch_candles("SOL", "hyperliquid", "1970-01-01T00:00:00+00:00")
    assert len(rows) == 4  # backfilled/upserted into the window

    assert len(telegram.sent) == 1
    text = telegram.sent[0]
    assert "SOL" in text and "公链" in text and "买入信号" in text

    await store.close()


async def test_prefers_hyperliquid_when_present(tmp_path):
    now_ms = int(time.time() * 1000)
    # Populate only Hyperliquid with candles; Binance exists but asset absent there.
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms), only_on="hyperliquid")
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem(symbol="SOL", tag="公链")], telegram,
        PriceWatchConfig(dry_run=False),
    )
    await watcher.tick()
    rows = await store.get_watch_candles("SOL", "hyperliquid", "1970-01-01T00:00:00+00:00")
    assert len(rows) == 4  # resolved on hyperliquid, fell back nothing
    await store.close()


async def test_dry_run_logs_but_does_not_send(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem(symbol="SOL", tag="公链")], telegram,
        PriceWatchConfig(dry_run=True),
    )
    await watcher.tick()
    assert telegram.sent == []  # dry-run: never delivered
    await store.close()


async def test_notify_sends_lifecycle_messages(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem(symbol="SOL", tag="公链")], telegram,
        PriceWatchConfig(dry_run=False),
    )
    await watcher._notify("🟢 启动")
    await watcher._notify("🔴 停止")
    assert telegram.sent == ["🟢 启动", "🔴 停止"]
    await store.close()


async def test_heartbeat_fires_only_when_due(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem(symbol="SOL", tag="公链")], telegram,
        PriceWatchConfig(dry_run=False, heartbeat_interval_seconds=3600),
    )
    watcher._last_tick_resolved = 1
    watcher._last_heartbeat = time.time() - 4000  # past the 1h window -> due
    await watcher._maybe_heartbeat()
    assert len(telegram.sent) == 1 and "运行中" in telegram.sent[0]
    await watcher._maybe_heartbeat()  # immediately again -> not due
    assert len(telegram.sent) == 1
    await store.close()


async def test_unresolvable_asset_skipped_after_first_pass(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem(symbol="NOPE", tag="x")], telegram,
        PriceWatchConfig(dry_run=False),
    )
    await watcher.tick()
    assert "NOPE" in watcher._unresolved
    # A second pass skips it (no re-resolve / no warning spam), so it stays marked.
    await watcher.tick()
    assert "NOPE" in watcher._unresolved
    await store.close()
