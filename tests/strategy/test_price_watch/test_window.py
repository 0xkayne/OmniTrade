"""Tests for the 7-day sliding-window math."""

from src.strategy.price_watch.window import latest_close, prune, window_extremes


def _c(ts, high, low, close):
    return {"ts": ts, "high": high, "low": low, "close": close}


def test_min_low_max_high_over_all():
    candles = [_c(1, 110, 90, 100), _c(2, 120, 95, 110), _c(3, 105, 85, 95)]
    assert window_extremes(candles, exclude_latest=False) == (85.0, 120.0)


def test_exclude_latest_ignores_fresh_break():
    # Last candle is a fresh low; with exclude_latest the prior low (100) is used.
    candles = [_c(1, 110, 100, 105), _c(2, 90, 50, 60)]
    assert window_extremes(candles, exclude_latest=True) == (100.0, 110.0)


def test_empty_and_single_candle():
    assert window_extremes([]) == (None, None)
    assert window_extremes([_c(1, 10, 5, 7)], exclude_latest=True) == (None, None)
    assert window_extremes([_c(1, 10, 5, 7)], exclude_latest=False) == (5.0, 10.0)


def test_missing_low_high_values_dropped():
    candles = [{"ts": 1, "high": 10, "low": None, "close": 9}, {"ts": 2, "high": 12, "low": 6, "close": 7}]
    assert window_extremes(candles, exclude_latest=False) == (6.0, 12.0)


def test_latest_close():
    candles = [_c(1, 110, 90, 100), _c(2, 120, 95, 405.5)]
    assert latest_close(candles) == 405.5
    assert latest_close([]) is None


def test_prune_drops_old_candles():
    now = 1000.0
    day = 86400.0
    candles = [_c(now - 8 * day, 1, 1, 1), _c(now - 6 * day, 2, 2, 2), _c(now, 3, 3, 3)]
    kept = prune(candles, now_ts=now, days=7)
    assert [c["ts"] for c in kept] == [now - 6 * day, now]
