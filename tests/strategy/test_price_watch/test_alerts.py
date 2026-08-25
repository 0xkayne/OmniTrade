"""Tests for the alert rule (break vs window low/high, event + re-arm)."""

from src.strategy.price_watch.alerts import AlertRule, AlertState, evaluate


def test_lower_break_fires_and_disarms():
    state = AlertState()
    msg = evaluate(AlertRule(0.10, 0.10), state, latest=90.0, min_low=100.0, max_high=120.0)
    assert msg is not None and "broke below" in msg
    assert state.armed is False
    assert state.last_direction == "lower"
    assert state.trigger_level == 90.0  # 100 * 0.9


def test_upper_break_fires():
    state = AlertState()
    msg = evaluate(AlertRule(0.10, 0.10), state, latest=132.0, min_low=100.0, max_high=120.0)
    assert msg is not None and "above" in msg
    assert state.last_direction == "upper"


def test_no_alert_within_threshold():
    state = AlertState()
    assert evaluate(AlertRule(0.10, 0.10), state, 105.0, 100.0, 120.0) is None
    assert state.armed is True


def test_fires_once_while_still_below():
    rule = AlertRule(0.10, 0.10)
    state = AlertState()
    assert evaluate(rule, state, 90.0, 100.0, 120.0) is not None
    # still below trigger -> no second alert
    assert evaluate(rule, state, 89.0, 100.0, 120.0) is None
    assert state.armed is False


def test_rearm_then_refire():
    rule = AlertRule(0.10, 0.10)
    state = AlertState()
    evaluate(rule, state, 90.0, 100.0, 120.0)  # fires, trigger=90
    # price recovers above the trigger -> re-arms (no alert that tick)
    assert evaluate(rule, state, 95.0, 100.0, 120.0) is None
    assert state.armed is True
    # a fresh break below a (new) 7d low of 100 fires again
    assert evaluate(rule, state, 89.0, 100.0, 120.0) is not None


def test_missing_extremes_returns_none():
    state = AlertState()
    assert evaluate(AlertRule(0.10, 0.10), state, latest=90.0, min_low=None, max_high=120.0) is None
    assert state.armed is True
