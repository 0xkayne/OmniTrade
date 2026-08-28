"""Tests for the backtest signal engine (drives a strategy per symbol)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.strategy.backtest.engine import BacktestEngine
from src.strategy.registry import get_strategy


def _candles(highs, closes, start_ms=1700000000000, step=300):
    base = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    out = []
    for i, (h, c) in enumerate(zip(highs, closes, strict=False)):
        ts = (base + timedelta(seconds=i * step)).isoformat()
        out.append({"ts": ts, "high": h, "low": h, "close": c})
    return out


def _engine() -> BacktestEngine:
    return BacktestEngine(
        lambda: get_strategy("pair_band", buy_drawdown_pct=0.10, sell_rise_pct=0.15,
                             window_days=5, cooldown_hours=0.0)
    )


def test_engine_emits_buy_on_drawdown():
    eng = _engine()
    candles = _candles([100, 100, 100, 99], [100, 100, 100, 89])
    sigs = eng.run_symbol("BTC", candles)
    assert any(s["direction"] == "buy" for s in sigs)
    buy = next(s for s in sigs if s["direction"] == "buy")
    assert buy["price"] == pytest.approx(89.0)


def test_engine_no_signal_within_trend():
    eng = _engine()
    assert eng.run_symbol("BTC", _candles([100, 100, 100], [100, 100, 100])) == []


def test_run_merges_and_sorts_by_time():
    eng = _engine()
    data = {
        "BTC": _candles([100, 100, 100, 99], [100, 100, 100, 89], start_ms=1000000000000),
        "ETH": _candles([50, 50, 50, 49], [50, 50, 50, 44], start_ms=2000000000000),
    }
    events = eng.run(data)
    times = [datetime.fromisoformat(e["ts"]).timestamp() for e in events]
    assert times == sorted(times)
    assert any(e["symbol"] == "BTC" for e in events)
