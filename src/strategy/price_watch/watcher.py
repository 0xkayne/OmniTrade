"""Price-watch daemon: fetch OHLCV, maintain a 7-day sliding window, alert via Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.strategy.price_watch.alerts import BandRule, BandSignal, BandState, evaluate_band
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
    window_days: int = 5
    buy_drawdown_pct: float = 0.10
    sell_rise_pct: float = 0.15
    signal_cooldown_hours: float = 6.0
    telegram_cmd_interval_seconds: int = 120
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
        master_chat_ids: list[str] | None = None,
    ) -> None:
        self._exchanges = exchanges
        self._registry = registry
        self._store = store
        self._watchlist = watchlist
        self._telegram = telegram
        self._cfg = config
        # Whitelist of "master" chat ids (from secrets) that may issue commands —
        # fixed at construction; broadcast recipients = masters ∪ db subscribers.
        self._master_chat_ids = list(
            master_chat_ids
            if master_chat_ids is not None
            else (getattr(telegram, "chat_ids", []) if telegram else [])
        )
        self._rule = BandRule(
            config.buy_drawdown_pct,
            config.sell_rise_pct,
            config.signal_cooldown_hours * 3600,
        )
        self._band_states: dict[str, BandState] = {}
        self._last_heartbeat: float | None = None
        self._last_tick_resolved: int = 0
        # Symbols that exist on no configured venue — permanently skipped for this
        # process so we stop re-resolving and spamming warnings every cycle.
        self._unresolved: set[str] = set()
        if not config.dry_run and telegram is None:
            raise ValueError("A TelegramSender is required when dry_run is False")

    async def run(self) -> None:
        """Daemon loop: notify start, backfill, tick every interval, heartbeat, notify stop."""
        await self._notify(f"🟢 价格监控已启动 · 标的 {len(self._watchlist)} 个 · {self._fmt_time(time.time())}")
        self._last_heartbeat = time.time()
        await self.backfill()
        logger.info(
            "Price watch daemon started: interval=%ss timeframe=%s window_days=%s "
            "buy_drawdown_pct=%.2f sell_rise_pct=%.2f",
            self._cfg.interval_seconds, self._cfg.timeframe, self._cfg.window_days,
            self._cfg.buy_drawdown_pct, self._cfg.sell_rise_pct,
        )
        cmd_task = asyncio.create_task(self._poll_telegram_commands())
        try:
            while True:
                t0 = time.perf_counter()
                await self.tick()
                await self._maybe_heartbeat()
                sleep_for = max(0.0, self._cfg.interval_seconds - (time.perf_counter() - t0))
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            cmd_task.cancel()
            # The Ctrl+C teardown closes the event loop we're running in, so a
            # notice sent from here would be dropped. The CLI sends the "stopped"
            # notice in a fresh loop after catching KeyboardInterrupt.
            logger.info("Price watch daemon stopped.")

    async def tick(self) -> None:
        """One scan: prune old rows, refresh each asset, evaluate + deliver alerts."""
        await self._prune()
        resolved = 0
        for item in self._watchlist:
            if item.symbol in self._unresolved:
                continue
            try:
                result = await self._refresh_asset(item)
                if result is None:
                    continue
                resolved += 1
                ev = self._evaluate_alert(item, result[1])
                if ev is None:
                    continue
                signal, min_low, max_high, now_ts = ev
                await self._deliver(item, signal, min_low, max_high, now_ts)
            except Exception:
                logger.exception("watch tick failed for %s", item.symbol)
        self._last_tick_resolved = resolved

    async def backfill(self) -> None:
        """Populate the 7-day window (no alert evaluation). Also prunes stale rows."""
        await self._prune()
        for item in self._watchlist:
            if item.symbol in self._unresolved:
                continue
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
        since_iso = self._iso_ago(self._cfg.window_days)
        since_ms = int(self._now_ms() - self._cfg.window_days * 86400_000)

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
                    limit=self._candles_per_day(self._cfg.timeframe) * self._cfg.window_days,
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

        # No venue had this instrument → treat as unresolvable for this process so we
        # stop re-resolving and spamming warnings every cycle. If the asset lists later,
        # restart the daemon to re-evaluate (the set resets on restart).
        self._unresolved.add(item.symbol)
        logger.info(
            "no instrument for %s on %s — skipped in future cycles", item.symbol, DEFAULT_VENUES
        )
        return None

    def _evaluate_alert(
        self, item: WatchItem, rows: list[dict]
    ) -> tuple[BandSignal, float, float, float | None] | None:
        min_low, max_high = window_extremes(rows, exclude_latest=False)
        latest = latest_close(rows)
        if latest is None or max_high is None:
            return None
        # Current bar timestamp (seconds) for the adjacent-signal cooldown.
        now_ts = None
        ts_str = rows[-1].get("ts") if rows else None
        if ts_str:
            try:
                now_ts = datetime.fromisoformat(str(ts_str)).timestamp()
            except ValueError:
                now_ts = None
        state = self._band_states.setdefault(item.symbol, BandState())
        signal = evaluate_band(self._rule, state, latest, max_high, now_ts)
        if signal is None:
            return None
        return signal, min_low, max_high, now_ts

    async def _deliver(
        self,
        item: WatchItem,
        signal: BandSignal,
        min_low: float,
        max_high: float,
        now_ts: float | None,
    ) -> None:
        await self._notify(self._format_signal(item, signal, min_low, max_high, now_ts))

    def _format_signal(
        self, item: WatchItem, signal: BandSignal, min_low: float, max_high: float, now_ts: float | None,
    ) -> str:
        sym, tag = item.symbol, item.tag
        t = self._fmt_time(now_ts)
        if signal.direction == "buy":
            dd = (1 - signal.price / signal.window_high) * 100
            return (
                f"🟢 买入信号 · {sym} · {tag}\n"
                f"时间: {t}\n"
                f"现价: {self._fmt_price(signal.price)}\n"
                f"{self._cfg.window_days}d 区间: {self._fmt_price(min_low)} – {self._fmt_price(max_high)}"
                f"   (自高点回撤 {dd:.1f}%)\n"
                f"触发线: {self._fmt_price(signal.trigger)} (高点×{self._rule.buy_drawdown_pct:.0%})\n"
                f"建议: 以现价买入"
            )
        rise = (signal.price / signal.buy_price - 1) * 100
        return (
            f"🔴 卖出信号 · {sym} · {tag}\n"
            f"时间: {t}\n"
            f"现价: {self._fmt_price(signal.price)}\n"
            f"买入价: {self._fmt_price(signal.buy_price)}  →  上涨 +{rise:.1f}%\n"
            f"触发线: {self._fmt_price(signal.trigger)} (买入价×{self._rule.sell_rise_pct:.0%})\n"
            f"建议: 以现价卖出（止盈）"
        )

    @staticmethod
    def _fmt_price(p: float | None) -> str:
        if p is None:
            return "—"
        a = abs(p)
        s = f"{p:.6f}" if a < 1 else (f"{p:.4f}" if a < 1000 else f"{p:,.2f}")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return f"${s}"

    @staticmethod
    def _fmt_time(ts: float | None) -> str:
        if ts is None:
            return "—"
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async def _notify(self, text: str) -> None:
        """Send a notice (alert / startup / shutdown / heartbeat); logs in dry-run."""
        if self._cfg.dry_run or self._telegram is None:
            logger.info("NOTIFY(dry-run): %s", text.replace("\n", " "))
            return
        await self._refresh_chat_ids()
        await self._telegram.send(text)

    async def _refresh_chat_ids(self) -> None:
        """Broadcast recipients = secrets masters ∪ dynamically-subscribed chats."""
        if self._telegram is None:
            return
        subs: list[str] = []
        try:
            subs = await self._store.list_subscribers()
        except Exception:
            logger.exception("list_subscribers failed")
        merged = list(dict.fromkeys([*self._master_chat_ids, *subs]))  # dedupe, keep order
        self._telegram.chat_ids = merged

    async def _poll_telegram_commands(self) -> None:
        """Background loop: poll getUpdates and apply whitelisted master commands."""
        if self._telegram is None:
            return
        offset = None
        while True:
            updates = await self._telegram.fetch_updates(offset)
            offset = await self._handle_telegram_updates(updates, offset)
            await asyncio.sleep(self._cfg.telegram_cmd_interval_seconds)

    async def _handle_telegram_updates(self, updates: list[dict], offset: int | None) -> int | None:
        """Process one batch of getUpdates; only whitelisted masters may command."""
        for upd in updates:
            offset = upd.get("update_id", offset)
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            from_id = str(msg.get("from", {}).get("id", ""))
            if not text or not chat_id or from_id not in self._master_chat_ids:
                continue  # only the whitelisted masters may command
            cmd = text.split()[0].lower()
            if cmd in ("/start", "/subscribe"):
                await self._store.add_subscriber(chat_id)
                await self._telegram.send_to(chat_id, "✅ 已订阅 ✓")
            elif cmd in ("/unsubscribe", "/stop"):
                await self._store.remove_subscriber(chat_id)
                await self._telegram.send_to(chat_id, "🚫 已退订")
            elif cmd == "/status":
                n = len(self._watchlist)
                subs = len(await self._store.list_subscribers())
                await self._telegram.send_to(
                    chat_id, f"📊 监控 {n} 个标的 · 订阅 {len(self._master_chat_ids) + subs} 个"
                )
        return offset

    async def _maybe_heartbeat(self) -> None:
        """Send a liveness message every heartbeat interval even when no alerts fire."""
        now = time.time()
        if self._last_heartbeat is None or (now - self._last_heartbeat) >= self._cfg.heartbeat_interval_seconds:
            self._last_heartbeat = now
            await self._notify(
                f"⏱ 价格监控运行中 · 标的 {len(self._watchlist)} 个 · "
                f"本轮取到 {self._last_tick_resolved} 个 · {self._fmt_time(time.time())}"
            )

    async def _prune(self) -> None:
        try:
            await self._store.prune_watch_candles(self._iso_ago(self._cfg.window_days))
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
