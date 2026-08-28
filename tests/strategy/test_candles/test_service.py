"""Unit tests for CandleService: venue resolution + incremental gap-fill + shared read.

Reuses MockExchange.set_ohlcv (slices by ``since``/``limit``) and records fetch
``since`` in ``fetch_ohlcv_calls`` so we can assert *how much* history was pulled.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.base_exchange import NetworkType
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange
from src.market.registry import InstrumentRegistry
from src.persistence.store import PersistenceStore
from src.strategy.backtest.data import BacktestDataLoader
from src.strategy.candles import CandleService
from src.strategy.price_watch.watchlist import WatchItem

DAY_MS = 86400_000


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


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


def _candles(start_ms: float, count: int, step_ms: float = DAY_MS) -> list:
    """Daily candles [ts, open, high, low, close, vol] starting at start_ms."""
    return [[start_ms + i * step_ms, 100, 101, 99, 100, 1] for i in range(count)]


async def _make_env(tmp_path, candles_hl, base_symbol="SOL", only_on="hyperliquid"):
    hl = MockExchange("hyperliquid")
    bn = MockExchange("binance")
    hl.set_ohlcv("SOL/USDT:USDT" if base_symbol == "SOL" else f"{base_symbol}/USDT:USDT", candles_hl)
    registry = InstrumentRegistry()
    registry.add(
        _make_perp("hyperliquid", base_symbol, "SOL/USDT:USDT" if base_symbol == "SOL" else f"{base_symbol}/USDT:USDT")
    )
    if only_on != "hyperliquid":
        registry.add(_make_perp("binance", base_symbol, base_symbol + "USDT"))
        bn.set_ohlcv(base_symbol + "USDT", candles_hl)
    exchanges = {"hyperliquid": hl, "binance": bn}
    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    return exchanges, registry, store


async def test_full_fill_on_empty_store(tmp_path):
    now_ms = int(time.time() * 1000)
    candles = _candles(now_ms - 4 * DAY_MS, 4)  # -4d ... -1d
    exchanges, registry, store = await _make_env(tmp_path, candles)
    service = CandleService(exchanges, registry, store, "5m")

    result = await service.ensure_filled(WatchItem("SOL", "公链"), since_days=5)

    assert result is not None and result.resolvable is True
    assert result.venue == "hyperliquid"
    assert len(result.rows) == 4
    rows = await store.get_watch_candles("SOL", "hyperliquid", "1970-01-01T00:00:00+00:00")
    assert len(rows) == 4  # everything fetched was persisted
    await store.close()


async def test_tail_only_fetch_when_window_covered(tmp_path):
    now_ms = int(time.time() * 1000)
    # Store already covers the 5-day window (earliest <= cutoff, latest > cutoff).
    candles = _candles(now_ms - 6 * DAY_MS, 7)  # -6d ... now
    exchanges, registry, store = await _make_env(tmp_path, candles)
    for c in candles:
        await store.upsert_watch_candle("SOL", "hyperliquid", _iso(c[0]), c[1], c[2], c[3], c[4])
    service = CandleService(exchanges, registry, store, "5m")
    hl = exchanges["hyperliquid"]

    result = await service.ensure_filled(WatchItem("SOL", "公链"), since_days=5)

    # Only the tail is fetched: since == the store's latest candle ts.
    assert result is not None and result.resolvable is True
    assert hl.fetch_ohlcv_calls[-1]["since"] == pytest.approx(now_ms, abs=2000)
    assert result.rows[0]["ts"] >= _iso(now_ms - 5 * DAY_MS)  # window cut to 5d, not 6d
    await store.close()


async def test_past_gap_fetch_from_cutoff(tmp_path):
    now_ms = int(time.time() * 1000)
    # Store only has 4 days of history, but we ask for 30 -> earliest > cutoff.
    stale = _candles(now_ms - 3 * DAY_MS, 4)  # -3d ... now
    exchanges, registry, store = await _make_env(tmp_path, stale)
    for c in stale:
        await store.upsert_watch_candle("SOL", "hyperliquid", _iso(c[0]), c[1], c[2], c[3], c[4])
    service = CandleService(exchanges, registry, store, "5m")
    hl = exchanges["hyperliquid"]

    result = await service.ensure_filled(WatchItem("SOL", "公链"), since_days=30)

    assert result is not None and result.resolvable is True
    assert hl.fetch_ohlcv_calls[-1]["since"] == pytest.approx(now_ms - 30 * DAY_MS, abs=2000)
    await store.close()


async def test_all_stale_fetch_from_cutoff(tmp_path):
    now_ms = int(time.time() * 1000)
    # Store holds only very old candles (latest <= cutoff). Mock exchange has fresh ones.
    stale = _candles(now_ms - 40 * DAY_MS, 5)  # -40d ... -36d
    fresh = _candles(now_ms - 1 * DAY_MS, 2)  # -1d ... now
    exchanges, registry, store = await _make_env(tmp_path, fresh)
    for c in stale:
        await store.upsert_watch_candle("SOL", "hyperliquid", _iso(c[0]), c[1], c[2], c[3], c[4])
    service = CandleService(exchanges, registry, store, "5m")
    hl = exchanges["hyperliquid"]

    result = await service.ensure_filled(WatchItem("SOL", "公链"), since_days=30)

    assert result is not None and result.resolvable is True
    # All stored data is older than the window -> refresh from cutoff.
    assert hl.fetch_ohlcv_calls[-1]["since"] == pytest.approx(now_ms - 30 * DAY_MS, abs=2000)
    await store.close()


async def test_unresolvable_asset_reports_not_resolvable(tmp_path):
    now_ms = int(time.time() * 1000)
    exchanges, registry, store = await _make_env(tmp_path, _candles(now_ms - 4 * DAY_MS, 4))
    service = CandleService(exchanges, registry, store, "5m")

    result = await service.ensure_filled(WatchItem("NOPE", "x"), since_days=5)

    assert result is not None
    assert result.resolvable is False
    assert result.venue == "" and result.rows == []
    await store.close()


async def test_backtest_loader_reads_the_shared_store(tmp_path):
    now_ms = int(time.time() * 1000)
    candles = _candles(now_ms - 4 * DAY_MS, 4)
    exchanges, registry, store = await _make_env(tmp_path, candles)
    service = CandleService(exchanges, registry, store, "5m")

    await service.ensure_filled(WatchItem("SOL", "公链"), since_days=5)
    data = await BacktestDataLoader(service, days=5).load([WatchItem("SOL", "公链")])

    assert set(data.keys()) == {"SOL"}
    assert len(data["SOL"]) == 4  # came out of the persisted store, not re-downloaded fresh
    await store.close()


async def test_batch_upsert_dedup_and_count(tmp_path):
    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    rows = [
        ("SOL", "hyperliquid", _iso(100), 1, 2, 0.5, 1.5),
        ("SOL", "hyperliquid", _iso(200), 2, 3, 1.5, 2.5),
        ("SOL", "hyperliquid", _iso(100), 1, 2, 0.5, 1.6),  # same (asset,venue,ts)
    ]
    n = await store.upsert_watch_candles(rows)  # one transaction for all three
    got = await store.get_watch_candles("SOL", "hyperliquid", "1970-01-01")
    assert n == 3
    assert len(got) == 2  # the duplicate ts=100 collapsed to one row
    assert [r["close"] for r in got if r["ts"] == _iso(100)] == [1.6]  # last writer wins
    await store.close()


async def test_near_complete_window_fetches_tail_only(tmp_path):
    now_ms = int(time.time() * 1000)
    # Store spans ~4.5 days (earliest is 12h newer than the 5-day cutoff). This is a
    # sub-day front hole (<= tolerance), so we must NOT re-download the whole window.
    ts = [now_ms - int(4.5 * DAY_MS), now_ms - int(3 * DAY_MS), now_ms - int(1.5 * DAY_MS), now_ms - 3600_000]
    candles = [[t, 100, 101, 99, 100, 1] for t in ts]
    exchanges, registry, store = await _make_env(tmp_path, candles)
    await store.upsert_watch_candles([("SOL", "hyperliquid", _iso(t), 100, 101, 99, 100) for t in ts])
    service = CandleService(exchanges, registry, store, "5m")
    hl = exchanges["hyperliquid"]

    result = await service.ensure_filled(WatchItem("SOL", "公链"), since_days=5)

    assert result is not None and result.resolvable is True
    # Tail-only: fetch `since` == the store's newest ts, NOT the 5-day cutoff.
    assert hl.fetch_ohlcv_calls[-1]["since"] == pytest.approx(now_ms - 3600_000, abs=2000)
    await store.close()
