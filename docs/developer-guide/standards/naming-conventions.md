---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/ 下所有模块的公开类、类型别名和模块级函数
---

# 命名规范

本文档定义 OmniTrade 全项目的命名规则，回答两个问题：

1. **看到一个名字，能不能判断它属于哪个功能模块？**
2. **能不能保证它不被误用、不被同名符号混淆？**

模块划分和依赖方向见[通用编码规范](code-standards.md)；本文只负责**名字**。

## 1. 大小写和单位后缀

- 类名使用 `PascalCase`：`InstrumentRegistry`、`RiskValidator`、`BacktestEngine`。
- 函数、方法和变量使用 `snake_case`：`fetch_many`、`quote_preference`、`planned_qty_base`。
- 常量使用全大写 `UPPER_SNAKE_CASE`：`PRODUCTS`、`TERMINAL_STATES`、`DEFAULT_VENUES`。
- 布尔值使用 `is_`、`has_`、`can_`、`should_` 或 `use_` 前缀：`is_acceptable`、`use_websocket`。
- 单位必须进入字段名：`*_id` 是稳定标识，`*_at` 是时间戳，`*_pct` 是百分比，`*_usd` 是美元金额，`*_qty` 是基础资产数量，`*_rate` 是费率或资金费率。
- `amount` 只在交易所适配器已经采用该术语时使用；领域模型用 `qty_base`、`notional_usd` 等带单位名称。

## 2. 包路径是唯一命名空间，不加模块前缀

类的归属由**包路径**表达，不由类名表达。禁止 `MarketInstrument`、`CoordIntent`、`ArbComparator` 这类把模块名写进类名的做法——`from src.market.instrument import Instrument` 里的限定词已经是包路径，重复一次不会增加信息，只会制造缩写黑话，并违反"不得创建源码中不存在的新层级"。

同理，**不要为了区分而给类名加层级词**（`Base`、`Core`、`Common`、`Shared`）。层级由目录表达。

## 3. 公开符号名必须全项目唯一

新增任何公开类、类型别名或模块级函数之前，先确认全项目没有同名符号：

```bash
grep -rn --include='*.py' -E '^(class|[A-Za-z_]+ +=) ' src/ | grep -w '<新名字>'
```

真发生冲突时，**用"拥有它的领域概念"消歧，不用模块名**。例如回测里那个裸 `Position`，拥有这个概念的是 `Portfolio`，所以叫 `PortfolioPosition`，而不是 `BacktestPosition`（那是模块名）。

## 4. 领域术语唯一性

以下名称是当前的正式术语，不得新造同义词：

| 正式名称 | 不要使用的同义词 | 说明 |
|---|---|---|
| `Intent` | request、task、trade request | 一次完整交易目标 |
| `Leg` | child order、slice、route | Intent 在单个交易所上的执行单元 |
| `Instrument` | market、pair、symbol | 可交易市场单元；原生 symbol 只是字段 |
| `Plan` | proposal、execution request | Planner 的输出 |
| `venue` | exchange（领域字段中） | 统一使用 `venue` 表示交易场所；适配器类名可保留 exchange |
| `product` | market_type（Intent 语境中） | 当前值为 `spot` 或 `perp` |
| `ROLLED_BACK_FAILED` | 新的状态名 `NEEDS_MANUAL` | `NEEDS_MANUAL` 仅作为用户说明别名 |
| `strategy` | algorithm、mode（策略语境中） | 产生信号或交易意图的组件 |

需要新概念时，先在[产品与领域约束](../design/sys-product-requirements.md)增加定义，再改代码；不要在某个局部模块中临时命名。

## 5. 共享词根：每个模块拥有一组领域词根

模块内的公开类型应含有所属模块的词根。词根取自**领域**，不取自模块名。这是"一眼看出归属"的主要机制。

### `src/market/` — Asset / Instrument / Quote

| 词根 | 符号 |
|---|---|
| 领域术语本身 | `Asset`、`Instrument`、`Quote` |
| `Instrument*` | `InstrumentRegistry`、`NetworkType`（网络是市场的属性） |
| `Quote*` | `QuoteFetcher`、`EstimatedFill` |
| `Pair*` | `PairMatcher`、`CrossVenuePair` |
| `FundingRateCache` | 费率缓存；与 `funding_arb` 的 `Funding*` 不冲突（这里是缓存，那里是策略） |

### `src/coordinator/` — Intent / Leg / Plan / 五阶段角色

| 词根 | 符号 |
|---|---|
| `Intent` | `Intent`、`LegConfig` |
| `Leg` | `LegExecution`、`LegReconciliation` |
| `Plan` | `Plan`、`PlannedLeg` |
| 阶段角色（**协调器专有，其他模块不得复用**） | `Planner`、`Validator`、`RiskValidator`、`Executor`、`Reconciler`、`Orchestrator` |
| `*Result` | `ValidationResult`、`RiskResult`、`ExecutionResult`、`ReconciliationResult` |
| 辅助 | `TimingCollector`、`is_valid_transition` |

### `src/exchange/` — Base / Ccxt / Orderbook / Mock

| 词根 | 符号 |
|---|---|
| `Base*` | `BaseExchange` |
| `CCXT*` | `CCXTExchange` |
| `Orderbook*` | `OrderbookCache` |
| `Mock*` | `MockExchange`（测试替身） |
| `ExchangeFactory` | 工厂 |

### `src/persistence/` — 表名常量 + `*Row`

| 词根 | 符号 |
|---|---|
| `*Row` | `IntentRow`、`LegRow`、`InstrumentRow` |
| `*_TABLE` / `*_INDEXES` | `INTENTS_TABLE`、`WATCH_CANDLES_INDEXES` 等 |
| 领域事件 | `AuditEvent` |
| 入口 | `PersistenceStore` |

`*Row` 是"持久化表的行形态"的固定标记，**不得用于其他含义**。

### `src/strategy/`（框架）— Bar / Signal / Strategy / Candle

| 词根 | 符号 |
|---|---|
| 领域术语本身 | `Bar`、`Signal`、`Strategy` |
| `Candle*` | `CandleService`、`DEFAULT_VENUES` |
| `Fill*` | `FillResult` |
| 注册表 | `register_strategy`、`get_strategy`、`list_strategies` |
| K 线工具 | `aggregate_candles`、`merge_coarse`、`coarse_trend`、`bar_contexts`、`ensure_derived`、`make_buy_prefilter` |

### `src/strategy/algos/` — 算法名

新增策略遵循固定形状：`<AlgoName>Params`（超参 dataclass）+ `<AlgoName>Strategy`（`Strategy` 子类，`name` ClassVar 用小写算法名）。现有 `PairBandParams`、`PairBandStrategy`。

### `src/strategy/funding_arb/` — Funding / Hedged / Arb

| 词根 | 符号 |
|---|---|
| `Funding*` | `FundingRateComparator`、`FundingRateMonitor`、`FundingSpread` |
| `Hedged*` | `HedgedPosition`、`HedgedPositionManager` |
| `Arb*` | `ArbConfig`、`ArbSignal`、`AutoArbRunner` |
| `Position*` | `PositionStatus` |
| 计算模型 | `NetReturn` |

### `src/strategy/price_watch/` — Watch / Band / Telegram

| 词根 | 符号 |
|---|---|
| `Watch*` | `WatchItem`（在框架根 `strategy/watchlist.py`） |
| `PriceWatch*` | `PriceWatcher`、`PriceWatchConfig` |
| `Band*` | `BandRule`、`BandState`、`BandSignal`、`evaluate_band`（在 `strategy/signals/band.py`） |
| `Telegram*` | `TelegramSender` |
| 窗口计算 | `window_extremes`、`latest_close`、`prune_window`、`load_watchlist` |

### `src/strategy/backtest/` — Backtest / Portfolio

| 词根 | 符号 |
|---|---|
| `Backtest*` | `BacktestEngine`、`BacktestDataLoader` |
| `Portfolio*` | `Portfolio`、`PortfolioPosition` |
| 指标 | `compute_metrics` |

### `src/strategy/trade_log/` — Trade

`TradeRecord`、`FIELDS`、`to_csv`、`to_json`。

### `src/cli/` — 命令名 + 装配

| 词根 | 符号 |
|---|---|
| Typer 命令函数 | 函数名即用户看到的命令名。子命令用 `<group>_<verb>`（`arb_scan`、`watch_run`、`trades_record`、`backtest_run`），顶层命令用纯动词（`order`、`query`、`cancel`、`ack`、`recover`） |
| `build_*` | `build_orchestrator`、`build_store`、`build_arb_scanner`、`build_price_watcher`、`build_backtest` |
| `parse_*` | `parse_split`、`parse_quote_preference` |
| `EXIT_*` | 退出码常量 |
| 程序化入口 | `submit_intent_from_dict`（`src/cli/agent_api.py`） |

### `src/observability/` — Metrics / Logging

`MetricsEmitter`、`NoopMetrics`、`setup_logging`、`JSONFormatter`、`StructuredLogger`。

### `src/legacy/`、`src/main.py`（legacy，见 §8）

`VolumeEngine`、`HedgePosition`、`ArbitrageEngine`、`ArbitrageOpportunity`、`TradeBot`、`VolumeTarget`、`HedgeVolumeStrategy`。

## 6. 角色后缀是保留词

一个后缀在全项目只能表示一种角色，**不得跨模块换意思**。新增后缀前先在本表登记。

| 后缀 | 唯一含义 | 现有 |
|---|---|---|
| `*Row` | 持久化表的行形态 | `IntentRow`、`LegRow`、`InstrumentRow` |
| `*Result` | 某个阶段的输出 | `ExecutionResult`、`ValidationResult`、`RiskResult`、`ReconciliationResult`、`FillResult`、`SplitResult` |
| `*Config` | 运行参数 | `ArbConfig`、`PriceWatchConfig` |
| `*Params` | 策略超参 | `PairBandParams` |
| `*Rule` | 触发阈值规则 | `BandRule` |
| `*State` | 可变状态 | `BandState` |
| `*Signal` | 信号 | `Signal`、`BandSignal`、`ArbSignal` |
| `*Manager` | 生命周期管理 | `HedgedPositionManager` |
| `*Service` | 无状态服务 | `CandleService` |
| `*Engine` | 批处理 / 回放主体 | `BacktestEngine` |
| `*Runner` | 长驻循环 | `AutoArbRunner` |
| `*Watcher` / `*Monitor` | 长驻监控 | `PriceWatcher`、`FundingRateMonitor` |
| `*Store` | 持久化入口 | `PersistenceStore` |
| `*Registry` | 注册表 | `InstrumentRegistry` |
| `*Cache` | 缓存 | `OrderbookCache`、`FundingRateCache` |
| `*Fetcher` | 取数 | `QuoteFetcher` |
| `*Matcher` | 配对 | `PairMatcher` |
| `*Comparator` | 比较 / 判定 | `FundingRateComparator` |
| `*Sender` | 对外发送 | `TelegramSender` |
| `*Emitter` | 事件发射 | `MetricsEmitter` |
| `*Collector` | 收集 | `TimingCollector` |
| `*Loader` | 装载 | `BacktestDataLoader` |
| `*Factory` | 构造工厂 | `ExchangeFactory` |

## 7. 模块级函数必须有宾语

格式 `<动词>_<宾语>`，宾语取自本模块词根或领域术语：

| 动词 | 用途 |
|---|---|
| `get_` / `find_` / `list_` / `load_` | 读取 |
| `fetch_` / `connect_` / `watch_` | 网络动作（必须 `async def`） |
| `save_` / `record_` | 写入 |
| `build_` / `create_` / `make_` | 构造 |
| `parse_` | 解析用户输入 |
| `evaluate_` / `compute_` | 纯计算 |
| `is_` / `has_` / `can_` / `should_` / `use_` | 布尔判断 |
| `to_` | 格式转换 |

**登记例外**：`src/strategy/mtf.py` 是纯时间工具模块，其中 `interval_ms`、`iso`、`iso_ms` 三个函数无宾语——在本模块内"时间"是唯一话题，加上宾语只是噪声。它们已登记为本规范的例外，**新增无宾语函数必须同样在本表登记**。

## 8. 易混名对照

以下名字相似但含义不同，禁止互相替换或简化：

| 名字 | 位置 | 含义 |
|---|---|---|
| `HedgePosition` | `src/legacy/volume_engine.py` | legacy 刷量引擎的持仓 |
| `HedgedPosition` | `src/strategy/funding_arb/position_manager.py` | 资金费率套利的对冲仓 |
| `PortfolioPosition` | `src/strategy/backtest/portfolio.py` | 回测组合的持仓 |

**不得再引入任何裸 `Position`。**

| 名字 | 位置 | 含义 |
|---|---|---|
| `Signal` | `src/strategy/base.py` | 策略输出的买卖信号（`Bar` 的产物） |
| `BandSignal` | `src/strategy/signals/band.py` | 波段状态机的一次触发 |
| `ArbSignal` | `src/strategy/funding_arb/comparator.py` | 套利方向（开 / 平 / 反向） |

| 名字 | 位置 | 含义 |
|---|---|---|
| `PositionStatus` | `funding_arb/position_manager.py` | 对冲仓的 `OPEN`/`CLOSING`/`CLOSED` |
| `INTENT_STATES` / `LEG_STATES` | `coordinator/state_machine.py` | Intent / Leg 的状态机状态 |
| `BandState` | `strategy/signals/band.py` | 波段持仓状态 |

## 9. 有意例外

以下命名**保留现状**，不按本规范改造：

- **legacy 代码**：`src/legacy/`、`src/main.py`。见[通用编码规范](code-standards.md) §6，保留现有命名以维持兼容性。新增业务代码不得放入这些位置。
- **交易所适配器**：沿用 CCXT 的 `symbol` / `amount` / `side` 等接口名，但这些名字**不得扩散到领域层**——领域层用 `Instrument` / `qty_base` / `notional_usd`。
- **局部变量**：`data`、`result`、`item` 等泛化名允许在很小的局部作用域使用；跨层参数必须用具体名称。

## 10. 变更流程

1. 定名之前先按 §3 检查唯一性。
2. 确认名字含有所属模块的词根（§5），或已在 §7 登记为例外。
3. 后缀必须取自 §6；需要新后缀时先登记再加代码。
4. 改名会同时影响 import、测试、CLI 输出和文档，**同一次变更内全部更新**。
5. 改名后用 `uv run --locked ruff check .` 和 `uv run --locked pytest -m "not network"` 验证。

## 11. 已完成的迁移（2026-09-10）

| 原名 | 新名 | 位置 | 原因 |
|---|---|---|---|
| `Signal`（`Literal` 别名） | `ArbSignal` | `funding_arb/comparator.py` | 与 `strategy.base.Signal` 同名不同物 |
| `Position` | `PortfolioPosition` | `backtest/portfolio.py` | 裸名，与两个 `Hedge(d)Position` 冲突 |
| `Status` | `PositionStatus` | `funding_arb/position_manager.py` | 与 Intent 状态、`BandState` 混淆 |
| `register` | `register_strategy` | `strategy/registry.py` | 模块级函数缺宾语 |
| `prune` | `prune_window` | `price_watch/window.py` | 模块级函数缺宾语 |
| `aggregate` | `aggregate_candles` | `strategy/mtf.py` | 模块级函数缺宾语 |
| `contexts` | `bar_contexts` | `strategy/mtf.py` | 模块级函数缺宾语 |

同时删除 `src/utils/logger.py`（零导入的死模块，其 `setup_logging` 与 `src/logging_setup.py` 同名冲突）。
