"""Tests for the paired-band rotation signals."""

from src.strategy.price_watch.alerts import BandRule, BandState, evaluate_band


def test_buy_on_drawdown_from_window_high():
    state = BandState()
    msg = evaluate_band(BandRule(0.10, 0.15), state, latest=90.0, window_high=100.0)
    assert msg is not None and "买入" in msg
    assert state.holding is True
    assert state.buy_price == 90.0  # assumed fill at the signal price


def test_sell_when_price_rises_above_buy_price():
    state = BandState(holding=True, buy_price=90.0)
    msg = evaluate_band(BandRule(0.10, 0.15), state, latest=104.0, window_high=100.0)
    # 90 * 1.15 = 103.5 → 104 sells (window_high is irrelevant once holding)
    assert msg is not None and "卖出" in msg
    assert state.holding is False
    assert state.buy_price is None


def test_no_action_within_threshold():
    state = BandState()
    assert evaluate_band(BandRule(0.10, 0.15), state, 95.0, 100.0) is None
    assert state.holding is False


def test_holding_waits_for_sell_never_rebuys():
    # While holding, only a sell is evaluated — a further dip is not a fresh buy.
    state = BandState(holding=True, buy_price=90.0)
    assert evaluate_band(BandRule(0.10, 0.15), state, 85.0, 100.0) is None
    assert state.holding is True


def test_can_buy_again_after_sell():
    rule = BandRule(0.10, 0.15)
    state = BandState(holding=True, buy_price=90.0)
    assert evaluate_band(rule, state, 104.0, 100.0) is not None  # sell
    assert state.holding is False
    # Flat again → a deeper drawdown triggers a fresh buy.
    assert evaluate_band(rule, state, 85.0, 100.0) is not None
    assert state.holding is True and state.buy_price == 85.0


def test_missing_inputs_returns_none():
    assert evaluate_band(BandRule(), BandState(), latest=None, window_high=100.0) is None
    assert evaluate_band(BandRule(), BandState(), latest=90.0, window_high=None) is None
