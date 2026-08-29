"""Tests for the pair_band strategy (on_bar behavior)."""

import pytest

from src.strategy.base import Bar
from src.strategy.strategies.pair_band import PairBandStrategy

T0 = "2026-08-22T00:00:00+00:00"
T1 = "2026-08-23T00:00:00+00:00"
T2 = "2026-08-24T00:00:00+00:00"
T3 = "2026-08-25T00:00:00+00:00"


def _bar(ts, c, h):
    return Bar(ts=ts, open=c, high=h, low=c, close=c)


def test_buy_on_drawdown_from_window_high():
    s = PairBandStrategy(buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0)
    s.on_bar(_bar(T0, 100, 100))
    s.on_bar(_bar(T1, 100, 100))
    sig = s.on_bar(_bar(T2, 89, 95))
    assert sig is not None and sig.direction == "buy"
    assert sig.price == pytest.approx(89.0)
    assert sig.metadata.get("window_high") == pytest.approx(100.0)


def test_sell_above_buy_price():
    s = PairBandStrategy(buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0)
    s.on_bar(_bar(T0, 100, 100))
    assert s.on_bar(_bar(T1, 89, 95)) is not None  # buy @ 89
    sig = s.on_bar(_bar(T2, 105, 105))  # 105 >= 89*1.15 = 102.35
    assert sig is not None and sig.direction == "sell"
    assert sig.metadata.get("buy_price") == pytest.approx(89.0)


def test_reset_clears_state():
    s = PairBandStrategy()
    s.on_bar(_bar(T0, 100, 100))
    s.on_bar(_bar(T1, 89, 95))  # buy
    s.reset()
    # After reset, an identical drawdown triggers a fresh buy (state was cleared).
    s.on_bar(_bar(T0, 100, 100))
    assert s.on_bar(_bar(T2, 89, 95)) is not None


def test_window_high_excludes_current_bar():
    s = PairBandStrategy(buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0)
    s.on_bar(_bar(T0, 100, 100))
    s.on_bar(_bar(T1, 100, 100))
    # The signal bar has the highest high (200); it must NOT feed window_high (no self-high lookahead).
    sig = s.on_bar(_bar(T2, 89, 200))
    assert sig is not None and sig.direction == "buy"
    assert sig.metadata.get("window_high") == pytest.approx(100.0)  # 200 excluded


def test_mtf_downtrend_gates_buy():
    from src.strategy.mtf import make_buy_prefilter

    s = PairBandStrategy(buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0)
    s.buy_prefilter = make_buy_prefilter("1d", 10)  # MTF gate injected (decoupled from the strategy)
    s.on_bar(Bar(ts=T0, open=100, high=100, low=100, close=100))
    s.on_bar(Bar(ts=T1, open=100, high=100, low=100, close=100))
    sig = s.on_bar(Bar(ts=T2, open=89, high=95, low=89, close=89, context={"coarse_trend": -1}))
    assert sig is None  # blocked by the daily downtrend
    assert s._state.holding is False  # state not mutated (no false holding)


def test_mtf_uptrend_allows_buy():
    from src.strategy.mtf import make_buy_prefilter

    s = PairBandStrategy(buy_drawdown_pct=0.10, sell_rise_pct=0.15, window_days=5, cooldown_hours=0.0)
    s.buy_prefilter = make_buy_prefilter("1d", 10)
    s.on_bar(Bar(ts=T0, open=100, high=100, low=100, close=100))
    s.on_bar(Bar(ts=T1, open=100, high=100, low=100, close=100))
    sig = s.on_bar(Bar(ts=T2, open=89, high=95, low=89, close=89, context={"coarse_trend": 1}))
    assert sig is not None and sig.direction == "buy"
