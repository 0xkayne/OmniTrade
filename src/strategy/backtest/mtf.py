"""Multi-timeframe (MTF) helpers: fine->coarse aggregation, hybrid merge, point-in-time trend.

Coarse bars are DERIVED from the base series (exact OHLCV aggregation) so any MTF
context is consistent with the base signal; where the base doesn't cover (deep
history), the independently-fetched store coarse bars are used as-is. The two are
merged into a single time-ordered series, with derived bars winning on overlap so
the recent window stays internally consistent.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_INTERVALS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def interval_ms(timeframe: str) -> int:
    """Milliseconds per bar for an interval like ``5m``/``1h``/``1d``."""
    m = re.match(r"^(\d+)([mhdw])$", timeframe)
    if not m:
        return 300_000
    return int(m.group(1)) * _INTERVALS[m.group(2)]


def iso_ms(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def aggregate(rows: list[dict], interval: str) -> list[dict]:
    """Bucket fine bars into ``interval`` and aggregate OHLCV exactly (UTC).

    open = first fine open, high = max, low = min, close = last fine close, volume = sum.
    Returns a time-ascending list of coarse candle dicts.
    """
    step = interval_ms(interval)
    buckets: dict[int, dict] = {}
    for r in rows:
        ts_ms = iso_ms(r["ts"])
        bucket = (ts_ms // step) * step
        o = float(r.get("open") or r.get("close") or 0.0)
        h = float(r.get("high") or o)
        lo = float(r.get("low") or o)
        c = float(r.get("close") or o)
        v = float(r.get("volume") or r.get("vol") or 0.0)
        b = buckets.get(bucket)
        if b is None:
            buckets[bucket] = {"ts": iso(bucket), "open": o, "high": h, "low": lo, "close": c, "volume": v}
        else:
            b["high"] = max(b["high"], h)
            b["low"] = min(b["low"], lo)
            b["close"] = c
            b["volume"] += v
    return [buckets[k] for k in sorted(buckets)]


def hybrid_coarse(base_rows: list[dict], store_coarse: list[dict], interval: str) -> list[dict]:
    """Merge base-derived coarse with store coarse, preferring the derived (consistent) bars."""
    merged: dict[int, dict] = {}
    for r in store_coarse:
        merged[iso_ms(r["ts"])] = r
    for r in aggregate(base_rows, interval):
        merged[iso_ms(r["ts"])] = r  # derived wins on the same bucket
    return [merged[k] for k in sorted(merged)]


def coarse_trend(coarse_rows: list[dict], ts: str, interval: str, sma_n: int) -> int:
    """Point-in-time trend of the last COMPLETED coarse bar vs its SMA_N at ``ts``.

    A coarse bar completes at the end of its bucket; at time ``ts`` the bar for the
    bucket containing ``ts`` is still forming, so we use only buckets strictly before
    it. Returns +1 (last completed close > its SMA_N, uptrend), -1 (downtrend), or
    0 (insufficient completed history -> neutral).
    """
    step = interval_ms(interval)
    ts_ms = iso_ms(ts)
    ts_bucket = (ts_ms // step) * step
    completed = [r for r in coarse_rows if iso_ms(r["ts"]) < ts_bucket]
    if len(completed) < sma_n:
        return 0
    closes = [float(r["close"]) for r in completed[-sma_n:]]
    return 1 if closes[-1] > sum(closes) / sma_n else -1
