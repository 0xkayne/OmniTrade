"""Sliding price-window helpers over a list of OHLCV candle dicts."""

from __future__ import annotations


def _safe_float(value: float | None) -> float | None:
    return float(value) if value is not None else None


def window_extremes(candles: list[dict], *, exclude_latest: bool = True) -> tuple[float | None, float | None]:
    """Return ``(min_low, max_high)`` over a list of candle dicts.

    Each candle dict must carry ``high`` / ``low``. When ``exclude_latest`` is
    True (the default for alert evaluation) the newest candle is ignored so the
    ``7-day low/high`` reflects the window *before* the move currently being
    judged — otherwise a fresh break would make itself the new extreme and the
    condition could never fire.
    """
    pool = candles[:-1] if exclude_latest else candles
    lows = [_safe_float(c.get("low")) for c in pool if c.get("low") is not None]
    highs = [_safe_float(c.get("high")) for c in pool if c.get("high") is not None]
    return (min(lows) if lows else None, max(highs) if highs else None)


def latest_close(candles: list[dict]) -> float | None:
    """Return the most recent candle's ``close`` (or None if empty)."""
    if not candles:
        return None
    return _safe_float(candles[-1].get("close"))


def prune(candles: list[dict], *, now_ts: float, days: int = 7) -> list[dict]:
    """Return candles with ts within the last ``days`` days (ts in seconds)."""
    cutoff = now_ts - days * 86400.0
    return [c for c in candles if _candle_ts(c) >= cutoff]


def _candle_ts(candle: dict) -> float:
    """Parse a candle's ``ts`` to epoch seconds (handles ISO string or numeric)."""
    raw = candle.get("ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        from datetime import datetime

        return datetime.fromisoformat(str(raw)).timestamp()
