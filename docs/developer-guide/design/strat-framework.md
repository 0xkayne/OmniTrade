---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/strategy/base.py, registry.py, algos/, candles.py, mtf.py
---

# 策略框架与共享设施

`src/strategy/` 是执行内核之上的策略层：这一层决定「要不要交易、交易多少」，但**不自己发单**——
需要成交时构造 `Intent` 交给 Coordinator（见[协调流程](base-coordination-pipeline.md)）。

本文描述四个功能域共用的抽象与数据设施。各功能域的正文见
[资金费率套利](strat-funding-arb.md)、[价格监控](strat-price-watch.md)、
[回测](strat-backtest.md)、[交易台账](strat-trade-log.md)。

## Strategy 契约

`src/strategy/base.py`：

| 类型 | 作用 |
|---|---|
| `Bar` | 一根 K 线：`ts`（ISO 时间戳）、`open`/`high`/`low`/`close`，外加 `context`（时点化的 MTF 特征） |
| `Signal` | 一次信号：`direction`（`buy`/`sell`）、`price`、`trigger`，外加 `metadata` |
| `Strategy` | 抽象基类：`name` 是注册键，`reset()` 清空标的级状态，`on_bar(bar)` 返回 `Signal` 或 `None` |

一个 `Strategy` 实例只对应**一个标的**，跨 bar 持有该标的的状态。实测两侧都依赖
「同一根 bar 重复喂入得到相同结果」：

- 实盘 watcher 先回放窗口内的历史 bar 再喂最新 bar，用这个方式重建状态（见[价格监控](strat-price-watch.md)）；
- 回测逐 bar 顺序喂入（见[回测](strat-backtest.md)）。

### 跨切面买入门 `buy_prefilter`

`Strategy.buy_prefilter` 是可选回调 `Callable[[Bar], bool]`，由引擎/守护进程注入，`None`
表示不拦截。任何策略都可以通过 `_allowed_buy(bar)` 复用它，而不必把趋势过滤写死在策略内部。
当前唯一的实现是 MTF 门（见下）。

## 策略注册表

`src/strategy/registry.py` 维护 `name -> Strategy 子类` 的映射：

- `register(cls)` — 类装饰器，按 `cls.name` 登记；
- `get_strategy(name, **params)` — 每次返回**新实例**（实例状态是每标的的，不能共享）；
- `list_strategies()` — 已注册的名字。

注册是**惰性**的：`get_strategy` / `list_strategies` 只在表为空时 `import src.strategy.algos`，
由 `algos/` 的 `@register` 完成登记。新增策略即在 `src/strategy/algos/` 下加模块并导入。

## 内置策略：`pair_band`

`src/strategy/algos/pair_band.py`，`name = "pair_band"`，参数见 `PairBandParams`
（`buy_drawdown_pct` / `sell_rise_pct` / `window_days` / `cooldown_hours`）。

它把「从近期窗口高点回撤」转成买入信号、「涨过买入价」转成卖出信号，状态机委托给
`price_watch.alerts.evaluate_band`：

1. `on_bar` 追加当前 bar，按 `window_days` 裁掉窗口外的旧 bar；bar 数不足 2 根时直接返回
   `None`（与 legacy 引擎一致，先有回看再判断）。
2. 窗口高点取 `self._bars[:-1]` 的最大 `high`——**排除当前信号 bar 自身**，否则一根新高
   会把自己变成新的高点，条件永远触发不了。
3. `evaluate_band(rule, state, bar.close, window_high, now, buy_allowed=...)`：
   空仓且 `close <= 窗口高点 × (1 - buy_drawdown_pct)` → BUY，记 `buy_price = close` 转入持仓；
   持仓且 `close >= buy_price × (1 + sell_rise_pct)` → SELL，回到空仓。

`evaluate_band` 内还有一道**相邻信号最小间隔**（`BandRule.min_signal_interval_seconds`，默认 6 小时）：
同一标的任意两个信号（买→卖、卖→买都算）间隔不足时本根静默，用于抑制高波动标的在窗口内
反复穿越触发线造成的噪声波段。`buy_allowed=False` 时禁止开仓，但**不改变状态**，等条件满足后再试。

`Signal` 与 `BandSignal` 的对应：`direction`/`price`/`trigger` 直接来自 `BandSignal`，
`buy_price` 与 `window_high` 放进 `Signal.metadata` 供告警文案使用。

## K 线服务：`CandleService`

`src/strategy/candles.py` 是**实盘 watcher 与回测共用的唯一 K 线入口**，两侧读写同一张
`watch_candles` 表（见[持久化层](base-persistence-layer.md)），所以回测回放的就是实盘累积的那份数据。

`ensure_filled(item, since_days=..., seed_days=..., timeframe=...)` 的流程：

1. 按 `DEFAULT_VENUES = ["hyperliquid", "binance"]` 的顺序解析标的所在 venue
   （`InstrumentRegistry.find_one`）。某所取不到该标的、或抓取抛错，就落到下一所。
2. 只抓缺的那一段：`_fetch_since_ms` 比较库里已有的最早/最晚 bar 与目标窗口。窗口前端
   1 天以内（`_FETCH_TOLERANCE_MS`）的空档视为已覆盖——Hyperliquid 只提供约 18 天的 5m
   历史且忽略 `since`，为不足一天的洞重下整窗没有意义。库里的最新 bar 比窗口还旧
   （进程停了很久）时，从那一根开始补，把重启空档补上而不是留成永久缺口。
3. 一次事务 upsert 整窗，再从库里读回调用方要的窗口。

`seed_days` 与 `since_days` 是两个不同的量：前者是**抓取深度**，只在库里从未有过该
`(asset, venue, interval)` 的 bar 时生效；后者是**读取窗口**。首次接触要深挖历史把序列
「养」到交易所允许的深度，但告警窗口不能被撑大，所以两者会分开计算。

区间是**各自独立的序列**：`timeframe` 作为行的 `interval` 参与读写，5m / 1h / 4h / 1d 永不混在一列。

抓取的分页与退避：

- Binance 单次 klines 上限 1000、ccxt 的 `paginate` 上限约 10000，所以 `_fetch_paged`
  按 1000 根向前翻页，直到填满窗口或追上当前 bar。其余 venue 只取一次——它们会对可取窗口
  做截断，翻页只是在同一个截断点上打转。
- `_fetch_with_retry` 对 `RateLimitExceeded` / `DDoSProtection` / `ExchangeNotAvailable` /
  `RequestTimeout` 退避重试：优先用交易所给的 `Retry-After`，否则 1s→2s→4s（上限 30s），
  最多 4 次，之后抛出，由调用方决定是否落到下一所。

返回值：

- `FillResult(venue, rows, resolvable)` — 一次填充的结果。
- `resolvable=False` 表示**没有任何已配置的 venue 持有该标的**，是永久条件，调用方可以缓存。
- 返回 `None` 表示标的解析到了但抓取失败/为空，是暂时条件，调用方下轮可以重试。

两者语义不同，不能合并处理（`strat-price-watch.md` 里的 `_unresolved` 缓存正是靠这个区分）。

`ensure_derived(asset, venue, interval)` 是 MTF 粗周期的持久化聚合入口。

## 多周期上下文（MTF）

`src/strategy/mtf.py` 为逐 bar 的信号提供粗周期背景。核心约束是**时点正确**：判断某个时刻的
趋势，只能用当时已经走完的粗 bar。

- 粗 bar 由基础序列**精确聚合**而来（`aggregate`：open 取首、high 取最大、low 取最小、
  close 取末、volume 求和，按 UTC 对齐到桶），保证与基础信号同源，不会出现两套 OHLC。
- 基础序列覆盖不到的更早历史，用独立抓取的粗周期序列补；两者按桶时间合并，
  **派生值覆盖存储值**（`merge_coarse`）。
- `coarse_trend(coarse_rows, ts, interval, sma_n)` 只取桶时间**严格早于** `ts` 所在桶的粗
  bar——`ts` 所在那个桶还在形成中——再看最后一根已完成粗 bar 的收盘价相对其 SMA_N，
  返回 `+1`（上升）/ `-1`（下降）/ `0`（已完成历史不足，中性）。
- `contexts(base_rows, derived_coarse, store_coarse, interval, sma_n)` 把逐 bar 的
  `{"coarse_trend": ...}` 对齐到基础序列；`interval` 为空则返回空上下文（功能关闭）。
- `ensure_derived(store, asset, venue, base_timeframe, interval)` **增量**维护持久化派生序列：
  重算最后一个（可能是部分的）桶及其之后的所有桶，再 `INSERT OR REPLACE` 写回 `derived_candles`。
  库里的基础周期是唯一事实来源。
- `make_buy_prefilter(mtf_interval, mtf_sma)` 返回上面说的 `buy_prefilter`：只在
  `coarse_trend != -1` 时放行买入。`mtf_interval` 为空则返回 `None`（不拦截）；上下文缺失
  或中性同样放行。

## 模块位置

| 文件 | 内容 |
|---|---|
| `src/strategy/base.py` | `Bar`、`Signal`、`Strategy` |
| `src/strategy/registry.py` | `register`、`get_strategy`、`list_strategies` |
| `src/strategy/algos/pair_band.py` | `PairBandParams`、`PairBandStrategy` |
| `src/strategy/candles.py` | `CandleService`、`FillResult`、`DEFAULT_VENUES` |
| `src/strategy/mtf.py` | `aggregate`、`merge_coarse`、`coarse_trend`、`contexts`、`ensure_derived`、`make_buy_prefilter` |
