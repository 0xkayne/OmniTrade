"""Price-watch daemon: fetch OHLCV, maintain a 7-day sliding window, alert via Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.strategy.price_watch.alerts import AlertRule, AlertState, evaluate
from src.strategy.price_watch.telegram import TelegramSender
from src.strategy.price_watch.watchlist import WatchItem
from src.strategy.price_watch.window import latest_close, window_extremes

logger = logging.getLogger(__name__)

DEFAULT_VENUES = ["hyperliquid", "binance"]
OHLCV_TYPE = {"perp": "swap", "spot": "spot"}


@dataclass
class PriceWatchConfig:
    """Runtime config for the price-watch daemon."""

    interval_seconds: int = 600
    timeframe: str = "5m"
    days: int = 7
    drop_pct: float = 0.10
    rise_pct: float = 0.10
    heartbeat_interval_seconds: int = 14400
    dry_run: bool = False


class PriceWatcher:
    """Fetch asset candles, maintain the window, evaluate and send alerts."""

    def __init__(
        self,
        exchanges: dict,
        registry,
        store,
        watchlist: list[WatchItem],
        telegram: TelegramSender | None,
        config: PriceWatchConfig,
    ) -> None:
        self._exchanges = exchanges
        self._registry = registry
        self._store = store
        self._watchlist = watchlist
        self._telegram = telegram
        self._cfg = config
        self._rule = AlertRule(config.drop_pct, config.rise_pct)
        self._alert_states: dict[str, AlertState] = {}
        self._last_heartbeat: float | None = None
        self._last_tick_resolved: int = 0
        if not config.dry_run and telegram is None:
            raise ValueError("A TelegramSender is required when dry_run is False")

    async def run(self) -> None:
        """Daemon loop: notify start, backfill, tick every interval, heartbeat, notify stop."""
        await self._notify(f"🟢 oneFill 价格监控已启动 · 标的 {len(self._watchlist)} 个")
        self._last_heartbeat = time.time()
        await self.backfill()
        logger.info(
            "Price watch daemon started: interval=%ss timeframe=%s days=%s drop_pct=%.2f rise_pct=%.2f",
            self._cfg.interval_seconds, self._cfg.timeframe, self._cfg.days,
            self._cfg.drop_pct, self._cfg.rise_pct,
        )
        try:
            while True:
                t0 = time.perf_counter()
                await self.tick()
                await self._maybe_heartbeat()
                sleep_for = max(0.0, self._cfg.interval_seconds - (time.perf_counter() - t0))
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            logger.info("Price watch daemon stopped.")
            # The CLI wraps asyncio.run() and swallows KeyboardInterrupt; keep the stop
            # notice alive past the cancellation so it isn't dropped.
            await asyncio.shield(self._notify("🔴 oneFill 价格监控已停止"))

    async def tick(self) -> None:
        """One scan: prune old rows, refresh each asset, evaluate + deliver alerts."""
        await self._prune()
        resolved = 0
        for item in self._watchlist:
            try:
                result = await self._refresh_asset(item)
                if result is None:
                    continue
                resolved += 1
                msg = self._evaluate_alert(item, result[1])
                if msg:
                    await self._deliver(item, msg)
            except Exception:
                logger.exception("watch tick failed for %s", item.symbol)
        self._last_tick_resolved = resolved

    async def backfill(self) -> None:
        """Populate the 7-day window (no alert evaluation). Also prunes stale rows."""
        await self._prune()
        for item in self._watchlist:
            try:
                await self._refresh_asset(item)
            except Exception:
                logger.exception("backfill failed for %s", item.symbol)

    async def close(self) -> None:
        """Close underlying exchange sessions."""
        for ex in self._exchanges.values():
            with contextlib.suppress(Exception):
                await ex.close()
        with contextlib.suppress(Exception):
            await self._store.close()

    # ── internals ────────────────────────────────────────────────

    async def _refresh_asset(self, item: WatchItem) -> tuple[str, list[dict]] | None:
        """Resolve the asset (Hyperliquid → Binance), fetch candles, upsert, return window rows."""
        since_iso = self._iso_ago(self._cfg.days)
        since_ms = int(self._now_ms() - self._cfg.days * 86400_000)

        for venue in DEFAULT_VENUES:
            if venue not in self._exchanges:
                continue
            inst = self._registry.find_one(
                base=item.symbol,
                venue=venue,
                market_type=item.market_type,
                quote_preference=item.quote_preference,
            )
            if inst is None:
                continue  # asset not on this venue → try next
            exchange = self._exchanges[venue]
            # Hyperliquid derives the market from the symbol (its fetch_ohlcv builds
            # the candleSnapshot body itself); add the market-type param only for
            # Binance, where swap candles need params={"type": "swap"}.
            candle_params = {"type": OHLCV_TYPE.get(item.market_type, "swap")} if venue == "binance" else {}
            try:
                candles = await exchange.fetch_ohlcv(
                    inst.venue_symbol,
                    timeframe=self._cfg.timeframe,
                    since=since_ms,
                    limit=self._candles_per_day(self._cfg.timeframe) * self._cfg.days,
                    params=candle_params,
                )
            except Exception as exc:
                logger.warning("fetch_ohlcv failed for %s/%s: %s", venue, inst.venue_symbol, exc)
                return None
            if not candles:
                logger.warning("no candles for %s on %s", item.symbol, venue)
                return None
            for c in candles:
                await self._store.upsert_watch_candle(
                    item.symbol, venue, self._iso_ms(c[0]), c[1], c[2], c[3], c[4]
                )
            rows = await self._store.get_watch_candles(item.symbol, venue, since_iso)
            return (venue, rows)

        logger.warning("no instrument found for %s on %s", item.symbol, DEFAULT_VENUES)
        return None

    def _evaluate_alert(self, item: WatchItem, rows: list[dict]) -> str | None:
        min_low, max_high = window_extremes(rows, exclude_latest=True)
        latest = latest_close(rows)
        if latest is None or min_low is None or max_high is None:
            return None
        state = self._alert_states.setdefault(item.symbol, AlertState())
        return evaluate(self._rule, state, latest, min_low, max_high)

    async def _deliver(self, item: WatchItem, msg: str) -> None:
        text = f"[{item.tag}] {item.symbol}\n{msg}"
        await self._notify(text)

    async def _notify(self, text: str) -> None:
        """Send a notice (alert / startup / shutdown / heartbeat); logs in dry-run."""
        if self._cfg.dry_run or self._telegram is None:
            logger.info("NOTIFY(dry-run): %s", text.replace("\n", " "))
            return
        await self._telegram.send(text)

    async def _maybe_heartbeat(self) -> None:
        """Send a liveness message every heartbeat interval even when no alerts fire."""
        now = time.time()
        if self._last_heartbeat is None or (now - self._last_heartbeat) >= self._cfg.heartbeat_interval_seconds:
            self._last_heartbeat = now
            await self._notify(
                f"⏱ oneFill 价格监控运行中 · 标的 {len(self._watchlist)} 个 · "
                f"本轮成功取到 {self._last_tick_resolved} 个"
            )

    async def _prune(self) -> None:
        try:
            await self._store.prune_watch_candles(self._iso_ago(self._cfg.days))
        except Exception:
            logger.exception("prune failed")

    @staticmethod
    def _now_ms() -> float:
        return datetime.now(timezone.utc).timestamp() * 1000

    @staticmethod
    def _candles_per_day(timeframe: str) -> int:
        """Candles per day for a timeframe like '5m' / '15m' / '1h'."""
        m = re.match(r"^(\d+)([mhdw])$", timeframe)
        if not m:
            return 288  # default: 5m
        minutes = int(m.group(1)) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[m.group(2)]
        return max(1, 1440 // minutes)

    @staticmethod
    def _iso_ago(days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    @staticmethod
    def _iso_ms(ms: float) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
