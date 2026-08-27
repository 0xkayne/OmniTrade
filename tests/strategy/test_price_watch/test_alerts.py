"""Tests for the paired-band rotation signals (with adjacent-signal cooldown)."""

from src.strategy.price_watch.alerts import BandRule, BandState, evaluate_band

T = 1000.0


def test_buy_on_drawdown_from_window_high():
    state = BandState()
    msg = evaluate_band(BandRule(0.10, 0.15), state, 90.0, 100.0, T)
    assert msg is not None and "买入" in msg
    assert state.holding is True
    assert state.buy_price == 90.0  # assumed fill at the signal price
    assert state.last_signal_ts == T


def test_sell_when_price_rises_above_buy_price():
    state = BandState(holding=True, buy_price=90.0)
    msg = evaluate_band(BandRule(0.10, 0.15), state, 104.0, 100.0, T)
    assert msg is not None and "卖出" in msg
    assert state.holding is False
    assert state.buy_price is None
    assert state.last_signal_ts == T


def test_no_action_within_threshold():
    state = BandState()
    assert evaluate_band(BandRule(0.10, 0.15), state, 95.0, 100.0, T) is None
    assert state.holding is False


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
    # Selling <6h after the buy is suppressed (min-hold).
    assert evaluate_band(rule, state, 105.0, 100.0, T + 2 * 3600) is None
    assert state.holding is True
    # Sell allowed >6h after the buy.
    assert evaluate_band(rule, state, 105.0, 100.0, T + 7 * 3600) is not None
    assert state.holding is False
    # Fresh dip 2h after the sell -> suppressed (adjacent signal too soon).
    assert evaluate_band(rule, state, 85.0, 100.0, T + 9 * 3600) is None
    assert state.holding is False
    # 9h after the sell -> allowed.
    assert evaluate_band(rule, state, 85.0, 100.0, T + 16 * 3600) is not None
    assert state.holding is True


def test_missing_inputs_returns_none():
    assert evaluate_band(BandRule(), BandState(), None, 100.0, T) is None
    assert evaluate_band(BandRule(), BandState(), 90.0, None, T) is None
