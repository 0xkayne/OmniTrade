"""Price-watch daemon: fetch OHLCV, maintain a 7-day sliding window, alert via Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.strategy.base import Bar, Signal, Strategy
from src.strategy.candles import DEFAULT_VENUES, CandleService
from src.strategy.price_watch.telegram import TelegramSender
from src.strategy.price_watch.watchlist import WatchItem
from src.strategy.price_watch.window import latest_close, window_extremes
from src.strategy.registry import get_strategy

logger = logging.getLogger(__name__)

OHLCV_TYPE = {"perp": "swap", "spot": "spot"}


@dataclass
class PriceWatchConfig:
    """Runtime config for the price-watch daemon."""

    interval_seconds: int = 600
    timeframe: str = "5m"
    strategy: str = "pair_band"
    window_days: int = 5
    history_days: int = 365  # startup seed horizon; used only on first contact (no stored candles)
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
            master_chat_ids if master_chat_ids is not None else (getattr(telegram, "chat_ids", []) if telegram else [])
        )
        self._strategies: dict[str, Strategy] = {}
        self._last_heartbeat: float | None = None
        self._last_tick_resolved: int = 0
        # Symbols that exist on no configured venue — permanently skipped for this
        # process so we stop re-resolving and spamming warnings every cycle.
        self._unresolved: set[str] = set()
        # Shared candle ingestion/read service (same store backtest reads).
        self._candles = CandleService(exchanges, registry, store, config.timeframe)
        if not config.dry_run and telegram is None:
            raise ValueError("A TelegramSender is required when dry_run is False")

    async def run(self) -> None:
        """Daemon loop: notify start, backfill, tick every interval, heartbeat, notify stop."""
        await self._notify(self._format_startup())
        self._last_heartbeat = time.time()
        # Start the Telegram command poller first, so /subscribe works even while
        # the (potentially slow) window backfill is still running.
        cmd_task = asyncio.create_task(self._poll_telegram_commands())
        await self.backfill()
        logger.info(
            "Price watch daemon started: interval=%ss timeframe=%s window_days=%s "
            "buy_drawdown_pct=%.2f sell_rise_pct=%.2f",
            self._cfg.interval_seconds,
            self._cfg.timeframe,
            self._cfg.window_days,
            self._cfg.buy_drawdown_pct,
            self._cfg.sell_rise_pct,
        )
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
        """One scan: refresh each asset, evaluate + deliver alerts."""
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
        """Populate the candle window (no alert evaluation)."""
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
        """Resolve the asset (Hyperliquid → Binance) and return the window rows.

        A ``None`` means the asset resolved but fetching failed/returned nothing
        this cycle (retried next tick). An unresolvable asset is cached so we stop
        re-resolving and spamming warnings every cycle.
        """
        result = await self._candles.ensure_filled(
            item, since_days=self._cfg.window_days, seed_days=self._cfg.history_days
        )
        if result is None:
            return None  # transient fetch failure → retry next cycle
        if not result.resolvable:
            self._unresolved.add(item.symbol)
            logger.info(
                "no instrument for %s on %s — skipped in future cycles",
                item.symbol,
                DEFAULT_VENUES,
            )
            return None
        return (result.venue, result.rows)

    def _evaluate_alert(self, item: WatchItem, rows: list[dict]) -> tuple[Signal, float, float, float | None] | None:
        min_low, max_high = window_extremes(rows, exclude_latest=False)
        latest = latest_close(rows)
        if latest is None or max_high is None:
            return None
        ts_str = rows[-1].get("ts") if rows else None
        now_ts = None
        if ts_str:
            try:
                now_ts = datetime.fromisoformat(str(ts_str)).timestamp()
            except ValueError:
                now_ts = None
        strategy = self._strategies.get(item.symbol)
        if strategy is None:
            strategy = get_strategy(
                self._cfg.strategy,
                buy_drawdown_pct=self._cfg.buy_drawdown_pct,
                sell_rise_pct=self._cfg.sell_rise_pct,
                window_days=self._cfg.window_days,
                cooldown_hours=self._cfg.signal_cooldown_hours,
            )
            # Seed the strategy's window with the lookback bars (minus the latest),
            # so its window_high reflects the full window, not just the newest bar.
            for row in rows[:-1]:
                strategy.on_bar(self._bar_of(row))
            self._strategies[item.symbol] = strategy
        signal = strategy.on_bar(self._bar_of(rows[-1]))
        if signal is None:
            return None
        return signal, min_low, max_high, now_ts

    @staticmethod
    def _bar_of(row: dict) -> Bar:
        return Bar(
            ts=str(row.get("ts")),
            open=float(row.get("open") or row.get("close") or 0.0),
            high=float(row.get("high") or 0.0),
            low=float(row.get("low") or 0.0),
            close=float(row.get("close") or 0.0),
        )

    async def _deliver(
        self,
        item: WatchItem,
        signal: Signal,
        min_low: float,
        max_high: float,
        now_ts: float | None,
    ) -> None:
        await self._notify(self._format_signal(item, signal, min_low, max_high, now_ts))

    def _format_signal(
        self,
        item: WatchItem,
        signal: Signal,
        min_low: float,
        max_high: float,
        now_ts: float | None,
    ) -> str:
        sym, tag = item.symbol, item.tag
        md = signal.metadata
        t = self._fmt_time(now_ts)
        if signal.direction == "buy":
            wh = md.get("window_high", max_high)
            dd = (1 - signal.price / wh) * 100
            return (
                f"🟢 买入信号 · {sym} · {tag}\n"
                f"时间: {t}\n"
                f"现价: {self._fmt_price(signal.price)}\n"
                f"{self._cfg.window_days}d 区间: {self._fmt_price(min_low)} – {self._fmt_price(wh)}"
                f"   (自高点回撤 {dd:.1f}%)\n"
                f"触发线: {self._fmt_price(signal.trigger)} (高点×{self._cfg.buy_drawdown_pct:.0%})\n"
                f"建议: 以现价买入"
            )
        bp = md.get("buy_price")
        rise = (signal.price / bp - 1) * 100 if bp else 0.0
        return (
            f"🔴 卖出信号 · {sym} · {tag}\n"
            f"时间: {t}\n"
            f"现价: {self._fmt_price(signal.price)}\n"
            f"买入价: {self._fmt_price(bp)}  →  上涨 +{rise:.1f}%\n"
            f"触发线: {self._fmt_price(signal.trigger)} (买入价×{self._cfg.sell_rise_pct:.0%})\n"
            f"建议: 以现价卖出（止盈）"
        )

    def _format_startup(self) -> str:
        """启动通知：按分类分组、列出全部监控标的，人类可读。"""
        lines = [f"🟢 价格监控已启动 · 共 {len(self._watchlist)} 个标的 · {self._fmt_time(time.time())}", ""]
        by_tag: dict[str, list[str]] = {}
        for item in self._watchlist:
            by_tag.setdefault(item.tag, []).append(item.symbol)
        for tag, syms in by_tag.items():
            lines.append(f"【{tag}】" + " · ".join(syms))
        return "\n".join(lines)

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
            elif cmd == "/log":
                chat_type = str(msg.get("chat", {}).get("type", ""))
                if chat_type != "private":
                    await self._telegram.send_to(chat_id, "⚠️ 交易录入仅在私聊可用")
                    continue
                await self._log_trade(chat_id, text)
        return offset

    async def _log_trade(self, chat_id: str, text: str) -> None:
        """Parse a minimal /log position-based trade and persist it (auto-match pnl)."""
        from src.strategy.trade_log.models import TradeRecord

        parts = text.split()[1:]  # drop the "/log" command word
        if len(parts) < 4:
            await self._telegram.send_to(
                chat_id, "⚠️ 至少需要: /log <symbol> <buy|sell> <qty> <price>\n" + self._trade_template()
            )
            return
        symbol, side = parts[0].upper(), parts[1].lower()
        if side not in ("buy", "sell"):
            await self._telegram.send_to(chat_id, "⚠️ side 为 buy|sell\n" + self._trade_template())
            return
        try:
            qty, price = float(parts[2]), float(parts[3])
            fee = float(parts[6]) if len(parts) > 6 else None
        except ValueError:
            await self._telegram.send_to(chat_id, "⚠️ qty/price/fee 需为数字\n" + self._trade_template())
            return
        venue = parts[4] if len(parts) > 4 else None
        tag = parts[5] if len(parts) > 5 else None
        strategy = parts[7] if len(parts) > 7 else None
        reason = parts[8] if len(parts) > 8 else None
        rec = TradeRecord(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            venue=venue,
            tag=tag,
            fee_usd=fee,
            strategy=strategy,
            reason=reason,
        )
        await self._store.record_trade(
            trade_id=rec.id,
            symbol=rec.symbol,
            side=rec.side,
            qty=rec.qty,
            price=rec.price,
            notional_usd=rec.notional_usd(),
            ts=rec.ts,
            venue=rec.venue,
            tag=rec.tag,
            fee_usd=rec.fee_usd,
            pnl_usd=None,
            strategy=rec.strategy,
            reason=rec.reason,
            note=rec.note,
        )
        msg = f"✅ 已记录 {rec.symbol} {rec.side} {rec.qty:g} @ {rec.price:g}"
        if side == "sell":  # record_trade auto-matched the buy and computed pnl
            row = await self._store.get_trade(rec.id)
            if row and row.get("pnl_usd") is not None:
                msg += f" · pnl {row['pnl_usd']:+.2f}"
        await self._telegram.send_to(chat_id, msg)

    @staticmethod
    def _trade_template() -> str:
        return (
            "📝 交易录入（极简）\n"
            "/log <symbol> <buy|sell> <qty> <price> [venue] [tag] [fee] [strategy] [reason]\n"
            "例: /log BTC buy 0.01 64000 hyperliquid 龙头 1.5 破位 回撤低吸\n"
            "前 4 个必填；fee 可不填；卖出会自动配对最近一笔买入并算盈亏"
        )

    async def _maybe_heartbeat(self) -> None:
        """Send a liveness message every heartbeat interval even when no alerts fire."""
        now = time.time()
        if self._last_heartbeat is None or (now - self._last_heartbeat) >= self._cfg.heartbeat_interval_seconds:
            self._last_heartbeat = now
            await self._notify(
                f"⏱ 价格监控运行中 · 标的 {len(self._watchlist)} 个 · "
                f"本轮取到 {self._last_tick_resolved} 个 · {self._fmt_time(time.time())}"
            )
