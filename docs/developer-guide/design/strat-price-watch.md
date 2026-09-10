---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/strategy/price_watch/ and onefill watch
---

# 价格监控

`onefill watch run` 是一个常驻守护进程：按固定间隔拉取 K 线，跑策略信号，把买/卖信号推送到
Telegram，并接受 Telegram 指令动态增删订阅者。它**不发送任何订单**——信号只到通知为止，
要成交由人去执行，或用 `trades` 记一笔（见[交易台账](strat-trade-log.md)）。

`onefill watch backfill` 复用同一套播种逻辑，只把历史灌进库、不做信号评估也不推告警。

## 装配

`PriceWatchConfig`（`watcher.py`）是运行参数，由 CLI 旗标填充：

| 字段 | 默认 | 说明 |
|---|---|---|
| `interval_seconds` | 600 | tick 间隔 |
| `timeframe` | `5m` | 基础周期 |
| `strategy` | `pair_band` | 注册表里的策略名（见[策略框架](strat-framework.md)） |
| `window_days` | 5 | 信号窗口长度 |
| `history_days` | 365 | 首次接触时的播种深度（只在不含已存 bar 时用） |
| `seed_intervals` | `["5m","1h","4h","1d"]` | 逐周期各自播种为独立序列 |
| `mtf_interval` / `mtf_sma` | `1d` / 10 | 粗周期买入门；`""` 关闭 |
| `buy_drawdown_pct` / `sell_rise_pct` | 0.10 / 0.15 | 策略参数 |
| `signal_cooldown_hours` | 6.0 | 相邻信号最小间隔 |
| `telegram_cmd_interval_seconds` | 120 | 指令轮询间隔 |
| `heartbeat_interval_seconds` | 14400 | 心跳间隔 |
| `dry_run` | false | 只记日志，不发 Telegram |

标的来自 `config/watchlist.yaml`，由 `load_watchlist` 解析成 `WatchItem`
（`symbol` + `tag` 必填，`market_type` 默认 `perp`，`quote_preference` 默认
`["USDT","USDC","USD"]`）。`tag` 是用户自定义分类，只用于告警文案分组。

`dry_run=False` 而没传 `TelegramSender` 时构造函数直接抛错。

## 运行循环

`PriceWatcher.run()`：

1. 推送启动通知（按 `tag` 分组列出全部标的）；
2. **先**起 Telegram 指令轮询任务，再跑 `backfill()`——播种可能很慢，但 `/subscribe`
   在这个过程中就应该可用；
3. 进入循环：`tick()` → `_maybe_heartbeat()` → 睡到下一个间隔。

`tick()` 逐标的做三件事，单个标的抛异常只记日志、不影响其余标的：

- `_refresh_asset(item)` — 取基础周期窗口（`window_days`）与 MTF 粗周期序列；
- `_evaluate_alert(...)` — 算信号；
- `_deliver(...)` — 推送。

`backfill()` 对每个 `seed_intervals` 里的周期各调一次 `CandleService.ensure_filled`
（`since_days=history_days`），每次填一个周期、每个周期是独立序列。首次运行把 365 天的
5m/1h/4h/1d 都灌进库，之后每次只补尾巴。

### 标的解析与 `_unresolved` 缓存

`ensure_filled` 的两种失败语义在这里被区别对待（见[策略框架](strat-framework.md)）：

- 返回 `None`（暂时性抓取失败）→ 本轮跳过，下轮重试；
- `resolvable=False`（没有任何已配置的 venue 持有该标的）→ 记入 `_unresolved` 集合，
  **本进程后续所有 tick / backfill 都不再尝试**，避免每轮重复解析并刷警告。

`_last_tick_resolved` 记录最近一轮成功取到多少标的，心跳消息里会带上。

### 信号评估

`_evaluate_alert(item, venue, rows, coarse_rows)`：

1. `window_extremes(rows, exclude_latest=False)` 取窗口内最低 `low` 与最高 `high`。
   这里 `exclude_latest=False`（与 `window_extremes` 的默认相反）——窗口极值只用于告警
   文案展示区间，不参与触发判断，触发用的窗口高点由策略自己在 `_bars[:-1]` 上算。
2. `latest_close(rows)` 取最新收盘；`rows[-1]["ts"]` 作为当前时间戳。
3. `ensure_derived` + `contexts` 计算逐 bar 的 `coarse_trend`（MTF 关闭时为空上下文）。
4. 取该标的的策略实例。**首次遇到该标的时**，先用 `get_strategy(...)` 建实例、注入
   `make_buy_prefilter(...)`，然后回放 `rows[:-1]` 把策略状态养到窗口开头，
   再喂 `rows[-1]` 取信号。之后每轮复用同一个实例（策略状态跨 tick 累积）。
5. 有信号则连同窗口极值和当前时间一起交给 `_deliver`。

回放这一步是必须的：`PairBandStrategy` 的窗口高点和持仓状态都在实例里，不回放就只剩
最后一根 bar，窗口高点会退化成单根 bar 的 high。

### MTF 粗周期序列

`_refresh_asset` 在 `mtf_interval` 非空时额外取一次粗周期序列，回溯深度取
`max(window_days, mtf_sma + 2)`——至少要有 `sma_n` 根已完成粗 bar 才可能算出趋势。
`mtf_interval` 为空则不做这一步，`contexts` 收到空列表，策略的 `buy_prefilter` 也是 `None`。

### 告警投递

`_notify(text)` 是所有对外消息（告警、启动、停止、心跳）的唯一出口：

- `dry_run=True` 或没有 sender → 只写日志；
- 否则先 `_refresh_chat_ids()`，再 `TelegramSender.send(text)` 广播。

`_refresh_chat_ids()` 把接收者设为 **secrets 里的 master 白名单 ∪ 数据库
`telegram_subscribers` 表**（去重保序），所以新订阅者不必重启进程就生效。
`_last_heartbeat` 保证就算没有任何信号，也会每 `heartbeat_interval_seconds` 发一条存活消息。

`_format_signal` 用 `Signal.metadata` 里的 `window_high` / `buy_price` 渲染中文文案
（买入信号带自高点回撤百分比，卖出信号带相对买入价的涨幅），价格按量级选小数位。

## Telegram 指令

指令轮询是 `run()` 里独立的后台任务，每 `telegram_cmd_interval_seconds` 调一次
`TelegramSender.fetch_updates(offset)`，交给 `_handle_telegram_updates` 处理，
用 `update_id` 推进 offset。

**权限**：只处理 `message.from.id` 在 `_master_chat_ids`（构造时从 secrets 固定）里的消息，
其余静默忽略。注意判断用的是发送者 `from.id`，不是会话 `chat.id`。

| 指令 | 行为 |
|---|---|
| `/start`、`/subscribe` | `add_subscriber(chat_id)`，回「已订阅」 |
| `/unsubscribe`、`/stop` | `remove_subscriber(chat_id)`，回「已退订」 |
| `/status` | 回监控标的数与订阅者数 |
| `/log ...` | 记一笔手工交易，**仅私聊**（群聊回拒绝提示） |

`/log` 的格式是
`/log <symbol> <buy|sell> <qty> <price> [venue] [tag] [fee] [strategy] [reason]`，
前 4 个必填。解析后构造 `TradeRecord` 并 `store.record_trade(...)`；`sell` 且未显式给
pnl 时由 store 自动配对最近一笔未匹配的 buy 并算 pnl，回执里会带上 pnl。写的是
CLI `onefill trades` 同一张表——详见[交易台账](strat-trade-log.md)。

`_trade_template()` 是格式提示文案，参数错误时随警告一起发出。

## 停机

`run()` 捕获 `asyncio.CancelledError`：取消指令轮询任务并记日志。
**不在这里发停止通知**——Ctrl+C 的收尾会关掉当前事件循环，从这里发的消息会被丢弃；
停止通知由 CLI 在捕获 `KeyboardInterrupt` 后于新的事件循环里发出。

`close()` 关闭各交易所会话与 store。

## 模块位置

| 文件 | 内容 |
|---|---|
| `src/strategy/price_watch/watcher.py` | `PriceWatchConfig`、`PriceWatcher` |
| `src/strategy/price_watch/watchlist.py` | `WatchItem`、`load_watchlist` |
| `src/strategy/price_watch/window.py` | `window_extremes`、`latest_close`、`prune` |
| `src/strategy/price_watch/alerts.py` | `BandRule`、`BandState`、`BandSignal`、`evaluate_band` |
| `src/strategy/price_watch/telegram.py` | `TelegramSender` |
