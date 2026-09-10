"""Tests for the backtest signal engine (drives a strategy per symbol)."""

from datetime import datetime, timezone

import pytest

from src.strategy.backtest.engine import BacktestEngine
from src.strategy.mtf import iso
from src.strategy.registry import get_strategy

DAY = 86_400_000
STEP = 300_000  # 5m in ms


def _bar(ts_ms: int, o: float, h: float, lo: float, c: float) -> dict:
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    return {"ts": ts, "open": o, "high": h, "low": lo, "close": c}


def _factory():
    return lambda: get_strategy(
        "pair_band", buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0
    )


def _engine(**kw) -> BacktestEngine:
    return BacktestEngine(_factory(), **kw)


def test_buy_fills_at_next_bar_open_plus_slippage():
    eng = _engine(slippage_pct=0.001)
    start = 1_700_000_000_000
    base = [
        _bar(start + 0 * STEP, 100, 100, 100, 100),
        _bar(start + 1 * STEP, 100, 100, 100, 100),
        _bar(start + 2 * STEP, 100, 100, 100, 100),
        _bar(start + 3 * STEP, 99, 99, 89, 89),  # buy signal (89 <= 100*0.9)
        _bar(start + 4 * STEP, 101, 101, 100, 100),  # fill at open 101
    ]
    sigs = eng.run_symbol("BTC", {"base": base, "coarse": {}})
    buy = next(s for s in sigs if s["direction"] == "buy")
    assert buy["ts"] == base[4]["ts"]  # executed on the NEXT bar
    assert buy["price"] == pytest.approx(101 * 1.001)  # open * (1 + slippage)


def test_sell_slippage_formula():
    eng = _engine(slippage_pct=0.001)
    assert eng._fill_price(100.0, "sell") == pytest.approx(99.9)  # 100 * (1 - 0.001)


def test_last_bar_signal_is_dropped_no_lookahead():
    eng = _engine()
    start = 1_700_000_000_000
    base = [
        _bar(start + 0 * STEP, 100, 100, 100, 100),
        _bar(start + 1 * STEP, 100, 100, 100, 100),
        _bar(start + 2 * STEP, 100, 100, 100, 100),
        _bar(start + 3 * STEP, 99, 99, 89, 89),  # buy signal on the LAST bar
    ]
    sigs = eng.run_symbol("BTC", {"base": base, "coarse": {}})
    assert not any(s["direction"] == "buy" for s in sigs)  # cannot execute without a next bar


def _drawdown_bundle(start: int, h: float, dip: float) -> dict:
    return {
        "base": [
            _bar(start + 0 * STEP, h, h, h, h),
            _bar(start + 1 * STEP, h, h, h, h),
            _bar(start + 2 * STEP, h, h, h, h),
            _bar(start + 3 * STEP, h - 1, h - 1, dip, dip),  # buy signal
            _bar(start + 4 * STEP, h, h, h, h),  # fill
        ],
        "coarse": {},
    }


def test_run_merges_and_sorts_by_time():
    eng = _engine()
    data = {
        "BTC": _drawdown_bundle(1_000_000_000_000, 100, 89),
        "ETH": _drawdown_bundle(2_000_000_000_000, 50, 44),
    }
    events = eng.run(data)
    times = [datetime.fromisoformat(e["ts"]).timestamp() for e in events]
    assert times == sorted(times)
    assert any(e["symbol"] == "BTC" for e in events)
    assert any(e["symbol"] == "ETH" for e in events)


def _daily(days: list[float]) -> list[dict]:
    return [
        {"ts": iso(1_700_000_000_000 + i * DAY), "open": 100, "high": 100, "low": 90, "close": c, "volume": 1}
        for i, c in enumerate(days)
    ]


def _sma_bundle(close_today: float) -> dict:
    start = 1_700_000_000_000 + 4 * DAY  # a day after the daily bars below
    return {
        "base": [
            _bar(start + 0 * STEP, 100, 100, 100, 100),
            _bar(start + 1 * STEP, 100, 100, 100, 100),
            _bar(start + 2 * STEP, 100, 100, 100, 100),
            _bar(start + 3 * STEP, 99, 99, 89, 89),  # would-be buy
            _bar(start + 4 * STEP, 101, 101, 100, 100),
        ],
        "coarse": {"1d": _daily(close_today)},
    }


def test_mtf_downtrend_gates_buy():
    # daily closes 100,99,98,97 -> last 97 < SMA3(99,98,97)=98 -> downtrend
    eng = _engine(mtf_intervals=("1d",), mtf_sma=3)
    sigs = eng.run_symbol("SOL", _sma_bundle([100, 99, 98, 97]))
    assert not any(s["direction"] == "buy" for s in sigs)


def test_mtf_uptrend_allows_buy():
    # daily closes 97,98,99,100 -> last 100 > SMA3(98,99,100)=99 -> uptrend
    eng = _engine(mtf_intervals=("1d",), mtf_sma=3)
    sigs = eng.run_symbol("SOL", _sma_bundle([97, 98, 99, 100]))
    assert any(s["direction"] == "buy" for s in sigs)
