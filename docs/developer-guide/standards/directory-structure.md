---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
applies_to: src/ 全部分包与模块；tests/ 的目录对应关系
---

# 代码目录结构规范

本文档定义 `src/` 的目标目录层级，以及每个目录允许收什么、禁止收什么。命名规则见[命名规范](naming-conventions.md)；与具体目录和名字无关的编码原则见[编码规范](code-standards.md)。

**当前代码符合本文档。** §9 记录了 2026-09-10 完成的迁移。新增模块时必须同步更新 §4 的目录树，
并确认 §3 的依赖表仍然成立。

## 1. 为什么需要层级

`src/` 的问题不是"文件放得不好看"，而是**从目录看不出谁依赖谁**。判断一个新模块该放哪里、一次改动会波及哪些包、哪些代码能安全删除，都应该只看目录就能回答。

因此本文档的每条规则都服务于三个可回答的问题：

1. 这个模块属于哪一层？
2. 它被允许依赖谁，被谁依赖？
3. 删掉它会不会波及别处？

## 2. 这些规则解决什么问题

分层不是审美问题。目录看不出依赖方向时，三个判断都会失准：新模块该放哪、一次改动会波及谁、
哪块代码能安全删除。

重构前 `src/` 有两组真实的**循环依赖**，它们是本规范大部分规则的由来：

- `market ↔ exchange`：市场层要 `NetworkType`，交易所层要 `Instrument`。解法是把 `NetworkType`
  放进 `market/`（它本来就是 `Instrument` 的字段），并让 `exchange → market` 成为唯一方向。
- `market ↔ persistence`：`InstrumentRegistry` 要 `PersistenceStore` 做缓存，而 `PersistenceStore`
  又要构造 `Instrument`。解法是 §5.3 的"只存不译"：持久化只读写列，转换归 `market/registry.py`。

另外 `src/core/` 曾同时装着全项目最被依赖的抽象和 1579 行只有上一代入口使用的引擎，
使一个应该最先被信任的包看起来"动不得"。结论写进了 §5.1：包的职责必须能用一句话说完。

## 3. 分层模型

依赖方向自下而上。**同一层的包之间不得互相导入，除非本表显式允许。**

```text
L4  cli/              onefill 命令行入口
        │
L3  strategy/         策略层：决定「要不要做、做多少」
L3  arbitrage/         跨所套利领域与独立双腿执行：机会、风控、恢复
        │
L2  coordinator/      执行内核：决定「怎么执行」
        │
L1  market/           市场域对象 + 行情访问 ────┐
    persistence/      行存储（不认识领域对象）  │ 允许 market → persistence
        │                                      │
L0  exchange/         交易所接入：唯一与外部交易场所通信的层
        │
X   observability/    横向：任何层可依赖，它不依赖任何业务层
```

**允许的跨层依赖**（且仅限这些）：

```text
cli          → strategy, arbitrage, coordinator, market, persistence, exchange, observability
strategy     → coordinator, market, persistence, exchange, observability
arbitrage    → market, exchange（读取 NetworkType/Instrument 和共享 OrderRequest；venue I/O 仍由注入的适配器完成）
coordinator  → market, persistence, exchange, observability
market       → persistence
exchange     → market, persistence
persistence  → （无业务依赖）
```

`exchange → market` 是唯一一处"下层导入上层"却合法的情况：适配器的职责就是把 venue 的原始市场数据**构造成领域对象**（`CCXTExchange.list_markets()` 产出 `Instrument`），这是端口-适配器方向。

**反过来 `market → exchange` 一律禁止。** 市场层只能对 exchange 做鸭子类型调用（例如 `QuoteFetcher` 只调 `exchange.fetch_*`，不导入其类型），需要类型标注时在本层声明 `Protocol`。

## 4. 目标目录树

```text
src/
  __init__.py

  exchange/                # L0 交易所接入
    base.py                #   BaseExchange 抽象
    ccxt.py                #   CCXTExchange（通用 CCXT 实现）
    binance.py             #   BinanceExchange（现货 / U 本位 / 币本位永续）
    binance_clients.py     #   Binance 固定产品客户端、网络校验与共享额度
    arcus.py               #   ArcusExchange（native 实现，接入后新增）
    factory.py             #   ExchangeFactory
    account_type.py        #   ccxt 账户类型 / 补偿单参数映射
    order.py               #   OrderCapabilities / OrderRequest / OrderSnapshot
    orderbook_cache.py     #   OrderbookCache（CCXT venue 的 WS 行情缓存）
    mock.py                #   MockExchange（测试替身）

  market/                  # L1 市场抽象（领域对象，不导入 exchange）
    instrument.py          #   Instrument + NetworkType
    asset.py               #   Asset
    quote.py               #   Quote、EstimatedFill
    registry.py            #   InstrumentRegistry
    quote_fetcher.py       #   QuoteFetcher（对 exchange 只做鸭子类型调用）
    pair_matcher.py        #   PairMatcher、CrossVenuePair
    funding_rate_cache.py  #   FundingRateCache

  persistence/             # L1 持久化（只读写行）
    schema.py              #   建表语句
    store.py               #   PersistenceStore、*Row

  arbitrage/               # L3 价差套利领域与受保护执行边界
    config.py              #   YAML 映射的类型化配置契约
    executor.py            #   offline/testnet 双腿执行协调器（主网拒绝）
    canary.py              #   单周期、确认串保护的测试网开仓后平仓入口
    recovery.py            #   重启查询、成交重建和敞口分类
    lifecycle.py           #   周期状态迁移、净敞口和 PnL
    models.py              #   配对、机会、周期和成交数据契约
    normalization.py       #   交易对、合约乘数和基础数量归一化
    profitability.py       #   深度 VWAP、费用和净价差计算
    risk.py                #   机会级预检查和限额判断
    scanner.py             #   并发读取多交易所报价

  coordinator/             # L2 执行内核
    intent.py              #   Intent、LegConfig
    plan.py                #   Plan、PlannedLeg
    state_machine.py       #   Intent/Leg 状态与合法转移
    planner.py             #   Planner
    validator.py           #   Validator
    risk.py                #   RiskValidator
    executor.py            #   Executor
    protection.py          #   LegProtection、保护价与报价检查
    leg_orders.py          #   LegOrderManager：持久化、发送、确认与撤单
    leg_context.py         #   执行上下文转换、原生成交量和持仓基线核对
    reconciler.py          #   Reconciler
    orchestrator.py        #   Orchestrator
    timing.py              #   TimingCollector

  strategy/                # L3 策略层
    base.py                #   Strategy、Bar、Signal
    registry.py            #   register_strategy / get_strategy
    candles.py             #   CandleService
    mtf.py                 #   多周期上下文
    watchlist.py           #   WatchItem / load_watchlist（框架、backtest、price_watch 共用）
    signals/               #   可复用信号算法：纯函数，无状态，不注册
      band.py              #     BandRule/BandState/BandSignal/evaluate_band
    algos/                 #   Strategy 适配器：注册进 registry，供 watch/backtest 按名取用
      pair_band.py         #     PairBandStrategy
    funding_arb/           #   功能域：资金费率套利
    price_watch/           #   功能域：价格监控
    backtest/              #   功能域：回测
    trade_log/             #   功能域：手工交易台账

  cli/                     # L4 入口
    main.py                #   Typer 应用与全部命令
    bootstrap.py           #   依赖装配（每类命令一个 build_*）
    config.py              #   入口配置读取、网络归一化和分网络凭据选择
    agent_api.py           #   程序化 Intent 提交入口

  observability/           # X 横向
    metrics.py             #   MetricsEmitter、NoopMetrics
    logging.py             #   结构化日志（原 src/logging_setup.py）
```

## 5. 各目录收录规则

### 5.1 `exchange/` — 交易所接入层

**收**：`BaseExchange` 接口、CCXT/native venue 适配器、构造适配器的工厂、可选的 `OrderbookCache`，以及所有只服务于“怎么跟交易所说话”的认证、序列化、错误和映射逻辑。

**不收**：任何下单编排（那是 `coordinator/`）、任何策略逻辑（那是 `strategy/`）。

**允许导入 `market/`**——适配器要把 venue 的原始数据构造成领域对象。这是本层唯一的"向上"依赖，且方向固定为 `exchange → market`。

`orderbook_cache.py` 从 `market/` 迁入：它为 CCXT venue 创建 ccxt.pro 实例、只做 WS 行情，是纯粹的 venue I/O。native adapter 可以提供自己的 WS 流，不必依赖该缓存。

`mock.py` 是**测试替身**，生产代码不得导入。它留在 `src/` 内而不是 `tests/`，是因为它实现 `BaseExchange` 的完整接口，必须与该接口同处一地才能在接口变化时立刻失效。

### 5.2 `market/` — 市场抽象层

**收**：`Asset`、`Instrument`、`Quote` 及其注册表、行情获取与缓存。

**不收**：具体 venue 的适配细节（`exchange/`）、策略逻辑（`strategy/`）、测试替身（`exchange/mock.py`）。

**禁止导入 `exchange/`。** 行情获取只对交易所对象做鸭子类型调用（`QuoteFetcher` 只调 `exchange.fetch_*`）。需要类型标注时，在本层用 `Protocol` 描述所需的最小接口，不要引入适配器类型。

`NetworkType` 定义在 `instrument.py`：它描述的是**市场属于哪个网络**，是 `Instrument` 的字段；`exchange/base.py` 从 `market/` 导入它，方向与 `exchange → market` 一致。这一条是解开 `market ↔ exchange` 环的关键。

`pair_matcher.py` 与 `funding_rate_cache.py` 留在 `market/`：前者配对的是 `Instrument`，后者缓存的是市场数据，都是市场概念，只是目前只被 `funding_arb` 消费。

### 5.3 `persistence/` — 持久化层

**收**：建表、迁移、行的读写、`*Row` 数据结构、审计事件。

**不收**：**任何业务层的类型。** 这是本节最重要的一条规则。

持久化层只存不译：它读写列，不构造 `Intent`、`Instrument`、`Quote` 等领域对象。"行 ↔ 领域对象"的转换由拥有该领域类型的层负责。

这条规则一次性解决三个违规边——`persistence` 不再需要 `BLOCKING_STATE`（状态语义归 `coordinator`）、不再需要 `Instrument`/`Asset`（转换归 `market/registry.py`）、不再需要 `NetworkType`（`network` 列按字符串存取）。

```python
# 现在（persistence 认识领域对象）—— 违规
async def load_instruments(self) -> list[Instrument]:
    from src.market.instrument import Instrument
    ...
    return [Instrument(...) for row in cursor]

# 目标（persistence 只返回行）
async def load_instrument_rows(self) -> list[InstrumentRow]:
    return [InstrumentRow(...) for row in cursor]
# 由 market/registry.py 把 InstrumentRow 转成 Instrument
```

### 5.4 `coordinator/` — 执行内核

**收**：`Intent`/`Plan` 及其状态机、五个执行阶段、编排器、计时。

**不收**：策略与信号（`strategy/`）、venue 适配细节（`exchange/`）。`account_type.py` 里的 ccxt 账户类型映射属于适配细节，移入 `exchange/`。

### 5.5 `strategy/` — 策略层

包根平铺的是**框架**（`base`/`registry`/`candles`/`mtf`），子包是**功能域**。三者的区别必须清楚：

| 位置 | 性质 | 规则 |
|---|---|---|
| `strategy/*.py` | 框架 | 不依赖任何功能域子包 |
| `strategy/signals/` | 可复用信号算法 | 纯函数 + 算法内部状态；不注册；不依赖任何功能域 |
| `strategy/algos/` | `Strategy` 适配器 | 继承 `Strategy`，通过 `register_strategy` 注册，供 `watch`/`backtest` 按名取用 |
| `strategy/<功能域>/` | 功能域 | 只允许依赖框架、`signals/`、`algos/` 和下层包；**功能域之间不得互相导入** |

`signals/` 与 `algos/` 的分工解决 `algos → price_watch` 这条违规边：`evaluate_band` 是纯算法，归 `signals/band.py`；`PairBandStrategy` 是适配器，归 `algos/pair_band.py`；`price_watch` 两个都依赖，但反过来不被依赖。

同一原则也把 `WatchItem` 提到了框架根：`strategy/watchlist.py` 同时被框架（`candles.py`）、
`backtest` 和 `price_watch` 使用，它不属于任何单一功能域。

**已知例外：`price_watch → trade_log`。** `price_watch/watcher.py` 的 Telegram `/log` 指令会构造
`TradeRecord` 写 `trades` 表，因此功能域之间存在这一条单向依赖。它的成因是 Telegram 指令分发
目前内嵌在 `PriceWatcher` 里；要消除这条边，需要把指令处理拆成独立模块，属于另一次改动。
在此之前它是**唯一**允许的功能域间依赖，其余一律禁止。

### 5.6 `cli/` — 入口层

**收**：Typer 应用、`build_*` 装配、`agent_api`，以及 `config.py` 的 YAML 读取和分网络凭据选择。
交易所工厂只消费字典，适配器不自行读取凭据文件。

**不收**：任何业务逻辑。命令函数应当只做参数解析 → 调 `bootstrap` 装配 → 调下层 → 渲染输出。

### 5.7 `observability/` — 横向

**收**：指标、结构化日志、追踪。横向层可以被任何层依赖，且**不得依赖任何业务层**。

`src/logging_setup.py` 移入此包，消除 `src/` 根目录下的游离模块。

## 6. 依赖规则

1. 只允许 §3 表中列出的边。新增跨包导入前先确认它在表内；不在表内就先改本文档并说明理由。
2. **同层包之间不得互相导入。** 目前只有 `market → persistence` 一个例外，已在表中显式列出。
3. **禁止环。** 任何 A→B 与 B→A 同时存在都视为错误。
5. 类型检查专用的 `if TYPE_CHECKING:` 导入**同样算依赖**——`market/registry.py` 对 `PersistenceStore` 的引用正是这类。

## 7. 文件与目录命名

- 包名 = 层名，单数：`exchange/`、`market/`、`persistence/`、`coordinator/`、`strategy/`、`cli/`。不使用复数。
- 文件名表达一个主要职责；包名已经表达的层次不重复进文件名（`exchange/base.py`，不是 `exchange/base_exchange.py`）。
- 一个文件原则上不超过 ~500 行。超过时先确认它是否混装了多个职责，而不是直接拆分。当前超限文件：`cli/main.py`（1853）、`exchange/ccxt.py`（1238）、`persistence/store.py`（1144）。

## 8. 测试目录对应关系

`tests/` **按模块镜像** `src/`，不按测试类型分目录：

```text
tests/
  exchange/     ← src/exchange/
  market/       ← src/market/
  persistence/  ← src/persistence/
  arbitrage/    ← src/arbitrage/
  coordinator/  ← src/coordinator/
  strategy/     ← src/strategy/
    signals/    ←   src/strategy/signals/
    algos/
    funding_arb/  price_watch/  backtest/  trade_log/
  cli/          ← src/cli/
  e2e/          # 跨模块的端到端测试，无对应源码目录
  fixtures/     # 共享测试数据
```

**测试骨架跟随副作用**：无副作用的阶段（Planner、Validator）用纯单测就够了；有副作用的
（Executor、Reconciler）需要 `MockExchange` + 内存 SQLite。判断一个新组件该配哪种，看它有没有副作用即可。

**测试放哪由被测模块决定，不由测试类型决定。** 当前实际使用的 marker 只有 `network` 和 `slow`
（在 `pyproject.toml` 注册）。`unit` / `integration` 这类按类型分的目录已在 §9 阶段 8 解散。

`tests/e2e/test_dex_testnet.py` 是 Arcus / Hyperliquid 专用真实测试网入口，运行编排位于
同目录的 `dex_testnet_runner.py`。两者 import 不做 I/O，网络用例默认只读；真实发单必须
显式传入 `--dex-testnet-trades`，受每笔和整轮预算约束，证据写入独立运行目录及数据库。
具体参数和验收边界见[DEX 测试网验证](../../user-guide/examples/dex-testnet-validation.md)。

## 9. 迁移记录（2026-09-10 完成）

分阶段执行，每阶段结束后测试全绿再进入下一阶段。先做低风险的搬运，最后做需要改行为的解耦。

阶段 2 建立的 `legacy/` 后来被整体删除（见 `git log -- src/legacy`）：它没有测试、没有调用方，
配置也与唯一的 venue 对不上，留着只会继续腐烂。表中保留该行是因为它记录的是本次迁移做过什么。

| 阶段 | 内容 | 结果 |
|---|---|---|---|
| 1 | 删除死代码：`utils/data_processor.py`、`funding_arb/premium_tracker.py` | 完成 |
| 2 | 建 `legacy/`，把 legacy 从 `core/`、`strategies_legacy/`、`utils/`、`main.py` 迁入；留 shim | 完成 |
| 3 | `core/` + `exchanges/` 合并为 `exchange/`，`account_type.py`、`mock_backend.py` 一并迁入 | 完成 |
| 4 | 解开 `market ↔ exchange` 环：`NetworkType` 移入 `market/instrument.py`；`orderbook_cache.py` 移入 `exchange/`；`quote_fetcher` 的 cache 参数改用本层 `Protocol` | 完成 |
| 5 | `src/logging_setup.py` → `observability/logging.py`；删除已空的 `utils/` | 完成 |
| 6 | `price_watch/alerts.py` → `strategy/signals/band.py`，改 `algos/pair_band.py` 与 `price_watch` 的导入 | 完成 |
| 7 | 按 §5.3 解耦 `persistence`：不再导入 `BLOCKING_STATE`、`Instrument`、`Asset`、`NetworkType` | 完成 |
| 8 | `tests/` 按 §8 重排；解散 `tests/unit/`、`tests/integration/` | 完成 |

阶段 3 和 4 都碰 `NetworkType`，必须按顺序做：阶段 3 先把 `core/` 改名，阶段 4 再把它挪进 `market/`。

阶段 7 是唯一改行为的阶段：`PersistenceStore.load_instruments()` 的返回类型变了，
`is_blocked_by_needs_manual()` 的归属从 `store` 移到了 `coordinator`。阻断语义由
`tests/coordinator/test_orchestrator.py::test_blocked_by_needs_manual` 覆盖（它用真实的
`PersistenceStore`，所以换 API 后仍然有效），`tests/persistence/test_blocking.py` 退化为对
`count_intents_with_status` 这个纯查询的测试。

## 10. 检查方式

§3 的依赖表和 §8 的测试目录对应关系由 **`tests/test_architecture.py` 断言**，每次 `pytest`
都会跑，不需要人工核对：

- `test_only_allowed_dependency_edges_exist` — 只允许 §3 登记过的边；新边必须先改本文件
- `test_import_graph_is_acyclic` — 模块级循环导入直接失败；靠惰性导入成立的环要显式登记
- `test_persistence_is_a_leaf` — §5.3 的"只存不译"
- `test_public_symbol_names_are_unique` — 见 [命名规范](naming-conventions.md) §3

类型检查专用的 `if TYPE_CHECKING:` 导入**同样算依赖**（§6 第 5 条），测试会算进去。

要看当前的边而不用等断言失败时：

```bash
uv run --locked pytest tests/test_architecture.py -v
```

新增模块时必须同时更新本文件 §4 的目录树，并确认 §3 的依赖表仍然成立——
如果新模块引入了未登记的边，上面第一个测试会直接失败并指出位置。
