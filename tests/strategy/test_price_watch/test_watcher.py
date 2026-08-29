"""Integration tests for PriceWatcher: fetch → persist → evaluate → deliver (mocked)."""

import time
from pathlib import Path

import pytest

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
    def __init__(self, chat_ids=None):
        self.chat_ids = list(chat_ids or [])
        self.sent = []
        self.sent_to = []

    async def send(self, text):
        self.sent.append(text)
        return True

    async def send_to(self, chat_id, text):
        self.sent_to.append((chat_id, text))
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


async def test_cfg_mtf_disabled_skips_coarse_fetch(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    item = WatchItem(symbol="SOL", tag="公链")
    watcher = PriceWatcher(
        exchanges, registry, store, [item], telegram,
        PriceWatchConfig(interval_seconds=600, dry_run=True, mtf_interval=""),
    )
    await watcher.tick()
    # MTF disabled -> the coarse (1d) series is not fetched into the store.
    d1 = await store.get_watch_candles("SOL", "hyperliquid", "1970-01-01", "1d")
    assert d1 == []
    await store.close()


def test_cfg_mtf_bar_of_attaches_context():
    row = {"ts": "2026-08-01T00:00:00+00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    assert PriceWatcher._bar_of(row, {"coarse_trend": -1}).context == {"coarse_trend": -1}
    assert PriceWatcher._bar_of(row).context == {}


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


async def test_handle_subscribe_adds_subscriber(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [{"update_id": 1, "message": {"text": "/subscribe", "chat": {"id": "GRP1"}, "from": {"id": "MASTER"}}}]
    await watcher._handle_telegram_updates(updates, None)
    assert await store.list_subscribers() == ["GRP1"]
    assert ("GRP1", "✅ 已订阅 ✓") in telegram.sent_to
    await store.close()


async def test_handle_ignores_non_master(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [{"update_id": 1, "message": {"text": "/subscribe", "chat": {"id": "GRP1"}, "from": {"id": "INTRUDER"}}}]
    await watcher._handle_telegram_updates(updates, None)
    assert await store.list_subscribers() == []  # ignored
    assert telegram.sent_to == []
    await store.close()


async def test_refresh_chat_ids_merges_masters_and_subscribers(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    await store.add_subscriber("GROUP1")
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    await watcher._refresh_chat_ids()
    assert telegram.chat_ids == ["MASTER", "GROUP1"]
    await store.close()


async def test_format_startup_groups_by_tag(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store,
        [WatchItem("BTC", "龙头"), WatchItem("SOL", "公链"), WatchItem("ETH", "龙头")],
        telegram, PriceWatchConfig(dry_run=False),
    )
    s = watcher._format_startup()
    assert "共 3 个标的" in s
    assert "【龙头】BTC · ETH" in s
    assert "【公链】SOL" in s
    await store.close()


async def test_log_trade_records_from_private_master(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [{"update_id": 1, "message": {
        "text": "/log BTC buy 0.01 64000 hyperliquid 龙头",
        "chat": {"id": "1526237659", "type": "private"}, "from": {"id": "MASTER"}}}]
    await watcher._handle_telegram_updates(updates, None)
    trades = await store.list_trades()
    assert len(trades) == 1 and trades[0]["symbol"] == "BTC"
    assert trades[0]["tag"] == "龙头" and trades[0]["venue"] == "hyperliquid"
    assert any("已记录" in t for _, t in telegram.sent_to)
    await store.close()


async def test_log_trade_rejected_in_group(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [{"update_id": 1, "message": {
        "text": "/log symbol=BTC side=buy qty=0.01 price=64000",
        "chat": {"id": "-1001", "type": "supergroup"}, "from": {"id": "MASTER"}}}]
    await watcher._handle_telegram_updates(updates, None)
    # /log must NOT record a trade from a group chat (only private masters).
    assert await store.list_trades() == []
    await store.close()


async def test_log_trade_missing_fields_shows_template(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [{"update_id": 1, "message": {
        "text": "/log BTC",
        "chat": {"id": "1526237659", "type": "private"}, "from": {"id": "MASTER"}}}]
    await watcher._handle_telegram_updates(updates, None)
    assert await store.list_trades() == []
    assert any("至少需要" in t for _, t in telegram.sent_to)
    await store.close()


async def test_log_trade_auto_matches_pnl(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store, telegram = await _make_env(tmp_path, _candles_with_break(now_ms))
    watcher = PriceWatcher(
        exchanges, registry, store, [WatchItem("SOL", "公链")], telegram,
        PriceWatchConfig(dry_run=False), master_chat_ids=["MASTER"],
    )
    updates = [
        {"update_id": 1, "message": {"text": "/log BTC buy 0.01 64000",
            "chat": {"id": "1526237659", "type": "private"}, "from": {"id": "MASTER"}}},
        {"update_id": 2, "message": {"text": "/log BTC sell 0.01 70400",
            "chat": {"id": "1526237659", "type": "private"}, "from": {"id": "MASTER"}}},
    ]
    await watcher._handle_telegram_updates(updates, None)
    trades = await store.list_trades()
    assert len(trades) == 2
    sells = [t for t in trades if t["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["pnl_usd"] == pytest.approx(64.0)  # (70400-64000)*0.01
    await store.close()
