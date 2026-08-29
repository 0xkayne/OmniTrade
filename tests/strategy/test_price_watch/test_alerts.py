"""Tests for the paired-band rotation signals (BandSignal + adjacent cooldown)."""

import pytest

from src.strategy.price_watch.alerts import BandRule, BandState, evaluate_band

T = 1000.0


def test_buy_on_drawdown_from_window_high():
    state = BandState()
    sig = evaluate_band(BandRule(0.10, 0.15), state, 90.0, 100.0, T)
    assert sig is not None and sig.direction == "buy"
    assert sig.price == 90.0 and sig.trigger == 90.0 and sig.window_high == 100.0
    assert state.holding is True
    assert state.buy_price == 90.0  # assumed fill at the signal price
    assert state.last_signal_ts == T


def test_sell_when_price_rises_above_buy_price():
    state = BandState(holding=True, buy_price=90.0)
    sig = evaluate_band(BandRule(0.10, 0.15), state, 104.0, 100.0, T)
    assert sig is not None and sig.direction == "sell"
    assert sig.price == 104.0 and sig.trigger == pytest.approx(103.5) and sig.buy_price == 90.0
    assert state.holding is False
    assert state.buy_price is None
    assert state.last_signal_ts == T


def test_no_action_within_threshold():
    assert evaluate_band(BandRule(0.10, 0.15), BandState(), 95.0, 100.0, T) is None


def test_holding_waits_for_sell_never_rebuys():
    state = BandState(holding=True, buy_price=90.0)
    assert evaluate_band(BandRule(0.10, 0.15), state, 85.0, 100.0, T) is None
    assert state.holding is True


def test_can_buy_again_after_sell():
    rule = BandRule(0.10, 0.15, min_signal_interval_seconds=0)  # no cooldown
    state = BandState(holding=True, buy_price=90.0)
    assert evaluate_band(rule, state, 104.0, 100.0, T) is not None  # sell
    assert state.holding is False
    assert evaluate_band(rule, state, 85.0, 100.0, T + 1) is not None  # fresh buy
    assert state.holding is True and state.buy_price == 85.0


def test_adjacent_signal_cooldown_suppresses_quick_roundtrip():
    # Adjacent-signal cooldown implies a min-hold (no sell <6h after buy) and
    # no re-buy <6h after a sell — kills sub-6h noise round-trips.
    rule = BandRule(0.10, 0.15, min_signal_interval_seconds=6 * 3600)
    state = BandState()
    assert evaluate_band(rule, state, 90.0, 100.0, T) is not None  # buy @T
    assert evaluate_band(rule, state, 105.0, 100.0, T + 2 * 3600) is None  # min-hold
    assert state.holding is True
    assert evaluate_band(rule, state, 105.0, 100.0, T + 7 * 3600) is not None  # sell
    assert state.holding is False
    assert evaluate_band(rule, state, 85.0, 100.0, T + 9 * 3600) is None  # <6h after sell
    assert state.holding is False
    assert evaluate_band(rule, state, 85.0, 100.0, T + 16 * 3600) is not None  # allowed
    assert state.holding is True


def test_missing_inputs_returns_none():
    assert evaluate_band(BandRule(), BandState(), None, 100.0, T) is None
    assert evaluate_band(BandRule(), BandState(), 90.0, None, T) is None


def test_buy_allowed_gates_buy():
    rule = BandRule(0.10, 0.15, min_signal_interval_seconds=0)
    # Default -> buy allowed.
    s = BandState()
    assert evaluate_band(rule, s, 90.0, 100.0, T) is not None and s.holding is True
    # buy_allowed=False -> buy blocked, state stays flat (no false holding).
    s = BandState()
    assert evaluate_band(rule, s, 90.0, 100.0, T, buy_allowed=False) is None
    assert s.holding is False and s.buy_price is None
    # buy_allowed=True -> buy allowed.
    s = BandState()
    assert evaluate_band(rule, s, 90.0, 100.0, T, buy_allowed=True) is not None and s.holding is True
