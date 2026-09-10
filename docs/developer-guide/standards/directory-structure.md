---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/ 全部分包与模块；tests/ 的目录对应关系
---

# 代码目录结构规范

本文档定义 `src/` 的目标目录层级，以及每个目录允许收什么、禁止收什么。命名规则见[命名规范](naming-conventions.md)；与目录无关的编码约定见[通用编码规范](code-standards.md)。

**当前代码尚未完全符合本文档。** §2 列出实测偏差，§9 给出迁移顺序。迁移完成前，本文档是**目标**而不是现状。

## 1. 为什么需要层级

`src/` 的问题不是"文件放得不好看"，而是**从目录看不出谁依赖谁**。判断一个新模块该放哪里、一次改动会波及哪些包、哪些代码能安全删除，都应该只看目录就能回答。

因此本文档的每条规则都服务于三个可回答的问题：

1. 这个模块属于哪一层？
2. 它被允许依赖谁，被谁依赖？
3. 删掉它会不会波及别处？

## 2. 现状问题（2026-09-10 实测）

### 2.1 `core/` 是杂物抽屉

`src/core/` 共 2642 行，其中 **1579 行（60%）是 legacy**，只有 `src/main.py` 引用：

| 文件 | 行数 | 性质 |
|---|---|---|
| `base_exchange.py` | 1000 | 全项目共享的抽象接口，被 30 个文件依赖 |
| `exchange_factory.py` | 63 | 共享工厂，却反向导入 `src/exchanges/` |
| `volume_engine.py` | 1473 | **仅 legacy 使用** |
| `arbitrage_engine.py` | 106 | **仅 legacy 使用** |

结果：`core/` 看起来是"最底层、动不得"的核心，实际上近六成是可以整体删除的旧代码。

### 2.2 legacy 散落在 4 个位置

`src/main.py`、`src/core/volume_engine.py`、`src/core/arbitrage_engine.py`、`src/strategies_legacy/`、`src/utils/log_utils.py`、`src/utils/network_manager.py`——全部只被 legacy 入口使用，却和现役代码混在同一批目录里，无法一次性删除。

### 2.3 依赖方向存在向上的边

| 违规边 | 位置 | 说明 |
|---|---|---|
| `market ↔ exchange` | `market/instrument.py:4` ⟷ `exchange/base.py:10` | 环：市场层取 `NetworkType`，交易所层取 `Instrument` |
| `market → coordinator` | `market/orderbook_cache.py:23` | 市场层导入执行内核的 `ccxt_account_type` |
| `market → persistence` | `market/registry.py:10` | 与 `persistence → market` 构成环 |
| `persistence → coordinator` | `persistence/store.py:12` | 持久化层导入 `BLOCKING_STATE` |
| `persistence → market` | `persistence/store.py:668` | 持久化层构造 `Instrument` 领域对象 |
| `persistence → core` | `persistence/store.py:13` | 持久化层导入 `NetworkType` |
| `core → exchanges` | `core/exchange_factory.py:5` | 底层反向导入上层 |
| `utils → core` | `utils/network_manager.py:1` | 工具包导入业务层 |
| `strategy.algos → strategy.price_watch` | `strategy/algos/pair_band.py:9` | 内置策略依赖某个功能子系统 |

`persistence` 同时依赖 `coordinator` 和 `market`，而 `market` 又依赖 `persistence`；`market` 与 `exchange` 互为依赖——**这两组环是本规范要消除的核心矛盾**。

### 2.4 死代码与名不副实的包

- `src/utils/data_processor.py`（228 行）——零消费者。
- `src/strategy/funding_arb/premium_tracker.py`（155 行）——零消费者。
- `src/utils/` 去掉死代码和 legacy 后**内容为空**。
- `src/market/mock_backend.py`（364 行）——`MockExchange` 是测试替身，只被 `tests/` 使用，却放在生产包里并被 `market/__init__.py` 导出。

## 3. 分层模型

依赖方向自下而上。**同一层的包之间不得互相导入，除非本表显式允许。**

```text
L4  cli/              onefill 命令行入口
        │
L3  strategy/         策略层：决定「要不要做、做多少」
        │
L2  coordinator/      执行内核：决定「怎么执行」
        │
L1  market/           市场域对象 + 行情访问 ────┐
    persistence/      行存储（不认识领域对象）  │ 允许 market → persistence
        │                                      │
L0  exchange/         交易所接入：唯一与外部交易场所通信的层
        │
X   observability/    横向：任何层可依赖，它不依赖任何业务层
    legacy/           待删除：整棵树可一次删掉
```

**允许的跨层依赖**（且仅限这些）：

```text
cli          → strategy, coordinator, market, persistence, exchange, observability
strategy     → coordinator, market, persistence, exchange, observability
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
  main.py                  # 兼容 shim，见 §5.8

  exchange/                # L0 交易所接入
    base.py                #   BaseExchange 抽象
    ccxt.py                #   CCXTExchange
    factory.py             #   ExchangeFactory
    account_type.py        #   ccxt 账户类型 / 补偿单参数映射
    orderbook_cache.py     #   OrderbookCache（自建 ccxt.pro 实例，WS 行情）
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

  coordinator/             # L2 执行内核
    intent.py              #   Intent、LegConfig
    plan.py                #   Plan、PlannedLeg
    state_machine.py       #   Intent/Leg 状态与合法转移
    planner.py             #   Planner
    validator.py           #   Validator
    risk.py                #   RiskValidator
    executor.py            #   Executor
    reconciler.py          #   Reconciler
    orchestrator.py        #   Orchestrator
    timing.py              #   TimingCollector

  strategy/                # L3 策略层
    base.py                #   Strategy、Bar、Signal
    registry.py            #   register_strategy / get_strategy
    candles.py             #   CandleService
    mtf.py                 #   多周期上下文
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
    agent_api.py           #   程序化 Intent 提交入口

  observability/           # X 横向
    metrics.py             #   MetricsEmitter、NoopMetrics
    logging.py             #   结构化日志（原 src/logging_setup.py）

  legacy/                  # X 待删除，整棵树可一次删掉
    main.py                #   TradeBot 入口
    volume_engine.py       #   刷量引擎
    arbitrage_engine.py    #   价差监控引擎
    hedge_volume.py        #   HedgeVolumeStrategy、VolumeTarget
    log_utils.py           #   控制台分段打印
    network_manager.py     #   网络切换
```

## 5. 各目录收录规则

### 5.1 `exchange/` — 交易所接入层

**收**：`BaseExchange` 接口、各 venue 的适配器、构造适配器的工厂、自建 ccxt.pro 实例的 `OrderbookCache`、以及所有只服务于"怎么跟交易所说话"的映射逻辑。

**不收**：任何下单编排（那是 `coordinator/`）、任何策略逻辑（那是 `strategy/`）。

**允许导入 `market/`**——适配器要把 venue 的原始数据构造成领域对象。这是本层唯一的"向上"依赖，且方向固定为 `exchange → market`。

`orderbook_cache.py` 从 `market/` 迁入：它自己创建 ccxt.pro 交易所实例、只做 WS 行情，是纯粹的 venue I/O，不是市场概念。

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

**收**：Typer 应用、`build_*` 装配、`agent_api`。

**不收**：任何业务逻辑。命令函数应当只做参数解析 → 调 `bootstrap` 装配 → 调下层 → 渲染输出。

### 5.7 `observability/` — 横向

**收**：指标、结构化日志、追踪。横向层可以被任何层依赖，且**不得依赖任何业务层**。

`src/logging_setup.py` 移入此包，消除 `src/` 根目录下的游离模块。

### 5.8 `legacy/` — 待删除

**收**：全部仅被 `python -m src.main` 使用的代码。这棵树应当满足：**没有 `legacy/` 之外的任何文件导入 `legacy/`**。

它是独立的兼容边界，不参与新架构的术语和目录扩展。删除时整棵树一次移除。

`src/main.py` 保留为一个三行 shim，以免改变已写进 `CLAUDE.md` 和用户文档的调用方式：

```python
"""Legacy entry point — the implementation lives in src/legacy/main.py."""
from src.legacy.main import main

if __name__ == "__main__":
    main()
```

## 6. 依赖规则

1. 只允许 §3 表中列出的边。新增跨包导入前先确认它在表内；不在表内就先改本文档并说明理由。
2. **同层包之间不得互相导入。** 目前只有 `market → persistence` 一个例外，已在表中显式列出。
3. **禁止环。** 任何 A→B 与 B→A 同时存在都视为错误。
4. **`legacy/` 只进不出。**
5. 类型检查专用的 `if TYPE_CHECKING:` 导入**同样算依赖**——`market/registry.py` 对 `PersistenceStore` 的引用正是这类。

## 7. 文件与目录命名

- 包名 = 层名，单数：`exchange/`、`market/`、`persistence/`、`coordinator/`、`strategy/`、`cli/`。不使用复数。
- 文件名表达一个主要职责；包名已经表达的层次不重复进文件名（`exchange/base.py`，不是 `exchange/base_exchange.py`）。
- 一个文件原则上不超过 ~500 行。超过时先确认它是否混装了多个职责，而不是直接拆分。当前超限文件：`cli/main.py`（1853）、`persistence/store.py`（1180）、`exchange/ccxt.py`（1237）、`legacy/volume_engine.py`（1473）。

## 8. 测试目录对应关系

`tests/` **按模块镜像** `src/`，不按测试类型分目录：

```text
tests/
  exchange/     ← src/exchange/
  market/       ← src/market/
  persistence/  ← src/persistence/
  coordinator/  ← src/coordinator/
  strategy/     ← src/strategy/
    signals/    ←   src/strategy/signals/
    algos/
    funding_arb/  price_watch/  backtest/  trade_log/
  cli/          ← src/cli/
  legacy/       ← src/legacy/
  e2e/          # 跨模块的端到端测试，无对应源码目录
  fixtures/     # 共享测试数据
```

**测试类型靠 pytest marker 区分，不靠目录**：`unit`、`integration`、`network`、`slow`、`mock`（已在 `pyproject.toml` 注册）。因此 `tests/unit/` 和 `tests/integration/` 这两个按类型分的目录应当解散，其内容并入对应模块目录。

## 9. 迁移顺序

分阶段进行，**每阶段结束后测试必须全绿**，不要一次性重排。先做低风险、高收益的搬运，再做需要改代码逻辑的解耦。

| 阶段 | 内容 | 风险 | 验证 |
|---|---|---|---|
| 1 | 删除死代码：`utils/data_processor.py`、`funding_arb/premium_tracker.py` | 低 | `pytest`；两者零消费者 |
| 2 | 建 `legacy/`，把 legacy 从 `core/`、`strategies_legacy/`、`utils/`、`main.py` 迁入；留 shim | 低 | `python -m src.main --mode volume --dry-run` 仍可启动；`pytest` |
| 3 | `core/` + `exchanges/` 合并为 `exchange/`，`account_type.py`、`mock_backend.py` 一并迁入 | 中（58 处 import，涉 37 个文件） | `pytest`；`ruff check` |
| 4 | 解开 `market ↔ exchange` 环：`NetworkType` 移入 `market/instrument.py`；`orderbook_cache.py` 移入 `exchange/`；`quote_fetcher` 的 cache 参数改用本层 `Protocol` | 中（NetworkType 与 BaseExchange 同文件，涉及 30 个文件的 import） | `pytest`；确认 `grep -rn 'src\.exchange' src/market/` 为空 |
| 5 | `src/logging_setup.py` → `observability/logging.py`；删除已空的 `utils/` | 低 | `pytest` |
| 6 | `price_watch/alerts.py` → `strategy/signals/band.py`，改 `algos/pair_band.py` 与 `price_watch` 的导入 | 低 | `pytest` |
| 7 | 按 §5.3 解耦 `persistence`：不再导入 `BLOCKING_STATE`、`Instrument`、`Asset`、`NetworkType` | **高**（涉及转换逻辑搬移） | `pytest`；`tests/persistence/` 需同步改写 |
| 8 | `tests/` 按 §8 重排；解散 `tests/unit/`、`tests/integration/` | 低 | 收集数不变，`pytest` 全绿 |

阶段 3 和 4 都碰 `NetworkType`，**必须按顺序做**：阶段 3 先把 `core/` 改名，阶段 4 再把它挪进 `market/`。

阶段 7 是唯一需要改行为的阶段：`PersistenceStore.load_instruments()` 的返回类型会变，`is_blocked_by_needs_manual()` 的归属会从 `store` 移到调用方。建议单独一个提交，并在 `coordinator/orchestrator.py` 增加针对阻断语义的测试。

## 10. 检查方式

```bash
# 依赖方向：列出全部跨包导入，人工核对 §3 的表
grep -rn --include='*.py' -E '^\s*from src\.[a-z_]+|^\s*import src\.[a-z_]+' src/ \
  | grep -v __pycache__ | sort

# legacy 只进不出
grep -rn --include='*.py' 'src\.legacy' src/ | grep -v '^src/legacy/'

# 无环（可用 pydeps 或自写脚本；当前手工核对）
uv run --locked pydeps src --no-show --cluster 2>/dev/null || true
```

新增模块时必须同时更新本文件 §4 的目录树，并确认 §3 的依赖表仍然成立。
