"""Tests for MTF helpers: fine->coarse aggregation, hybrid merge, point-in-time trend."""

from src.strategy.mtf import (
    aggregate,
    coarse_trend,
    contexts,
    ensure_derived,
    interval_ms,
    iso,
    make_buy_prefilter,
    merge_coarse,
)

M = 60_000
H = 3_600_000
D = 86_400_000


def _row(ts, o, h, lo, c, v=1):
    return {"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v}


def test_interval_ms():
    assert interval_ms("5m") == 5 * M
    assert interval_ms("1h") == H
    assert interval_ms("1d") == D
    assert interval_ms("1w") == 7 * D


def test_aggregate_six_5m_to_1h():
    epoch = H * 10  # 10:00 UTC
    rows = [
        _row(iso(epoch), 100, 101, 99, 100),
        _row(iso(epoch + 5 * M), 100, 103, 100, 102, 2),
        _row(iso(epoch + 10 * M), 102, 102, 98, 98),
        _row(iso(epoch + 15 * M), 98, 99, 97, 99),
        _row(iso(epoch + 20 * M), 99, 105, 99, 104, 3),
        _row(iso(epoch + 25 * M), 104, 104, 100, 101),
    ]
    out = aggregate(rows, "1h")
    assert len(out) == 1
    b = out[0]
    assert (b["open"], b["high"], b["low"], b["close"], b["volume"]) == (100, 105, 97, 101, 9)


def test_merge_coarse_prefers_derived_over_store():
    # a coarse daily bar already aggregated for day 10 (high 110) overrides the store's day-10 bar.
    derived = [_row(iso(10 * D), 100, 110, 90, 105)]
    store1d = [
        _row(iso(9 * D), 95, 96, 94, 95),
        _row(iso(10 * D), 99, 100, 98, 99),
    ]
    out = merge_coarse(derived, store1d)
    assert len(out) == 2
    assert out[0]["ts"] == iso(9 * D)
    assert out[1]["ts"] == iso(10 * D)
    assert out[1]["high"] == 110  # derived won (store day-10 bar had high=100)


def test_coarse_trend_point_in_time():
    down = [_row(iso(i * D), c, c, c, c) for i, c in enumerate([100, 99, 98, 97])]
    # On day 4 (all 4 bars completed) -> last 97 < SMA3(99,98,97)=98 -> downtrend.
    assert coarse_trend(down, iso(4 * D + H), "1d", 3) == -1
    # On day 1 only day 0 is completed -> insufficient (needs 3) -> neutral.
    assert coarse_trend(down, iso(1 * D + H), "1d", 3) == 0
    # On day 3 the day-3 bar is forming (excluded); completed = days 0-2 close [100,99,98],
    # last 98 < (100+99+98)/3 = 99 -> downtrend.
    assert coarse_trend(down, iso(3 * D + H), "1d", 3) == -1
    # Uptrend series.
    up = [_row(iso(i * D), c, c, c, c) for i, c in enumerate([97, 98, 99, 100])]
    assert coarse_trend(up, iso(4 * D + H), "1d", 3) == 1


def test_contexts_aligned_to_base():
    derived = [_row(iso(i * D), c, c, c, c) for i, c in enumerate([100, 99, 98, 97])]
    base = [_row(iso(4 * D + H), 100, 100, 100, 100), _row(iso(4 * D + 2 * H), 100, 100, 100, 100)]
    ctx = contexts(base, derived, [], "1d", 3)  # derived = 5m-covered coarse; store = deep history
    assert len(ctx) == len(base)  # aligned to base rows
    assert ctx[0]["coarse_trend"] == -1  # 4 completed dailies, downtrend
    # Disabled interval -> empty contexts (gate inert).
    assert contexts(base, derived, [], "", 3) == [{}, {}]


async def test_ensure_derived_incremental(tmp_path):
    from pathlib import Path

    from src.persistence.store import PersistenceStore

    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    epoch = H * 10  # 10:00 UTC
    # 6 five-minute bars within one hour -> exactly one derived 1h bar.
    for i in range(6):
        await store.upsert_watch_candle(
            "SOL", "hyperliquid", iso(epoch + i * 5 * M), 100, 105, 95, 100 + i, interval="5m"
        )
    d1 = await ensure_derived(store, "SOL", "hyperliquid", "5m", "1h")
    assert len(d1) == 1
    assert d1[0]["close"] == 105  # last 5m close aggregates into the hourly close
    assert d1[0]["high"] == 105 and d1[0]["low"] == 95
    # Second call with no new 5m -> no duplicate (incremental, unchanged).
    d2 = await ensure_derived(store, "SOL", "hyperliquid", "5m", "1h")
    assert len(d2) == 1
    await store.close()


def test_make_buy_prefilter():
    from src.strategy.base import Bar

    assert make_buy_prefilter("", 10) is None  # disabled
    pref = make_buy_prefilter("1d", 10)
    assert pref(Bar(ts="t", open=1, high=1, low=1, close=1, context={"coarse_trend": -1})) is False
    assert pref(Bar(ts="t", open=1, high=1, low=1, close=1, context={"coarse_trend": 1})) is True
    assert pref(Bar(ts="t", open=1, high=1, low=1, close=1)) is True  # missing context -> allow
