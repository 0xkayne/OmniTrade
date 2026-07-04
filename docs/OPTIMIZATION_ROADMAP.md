# oneFill — 下一阶段优化路线图

> Status: Draft v0.1 · Author: kayne · Date: 2026-05-31
>
> 本文档定义 oneFill MVP 交付后的优化路线。阅读前提：`docs/PRD.md`（产品规格）、`docs/REFACTOR_PLAN.md`（实现计划）、`docs/STATUS.md`（当前交付状态）。

---

## 1. 目标定位

MVP 实现了"跨所协调下单 + 自动补偿"的基本能力，但要**将其变成一个能持续产生真实收益的工具**，必须解决两个致命短板：

- **时延**：Plan 阶段的行情拉取、Execute 阶段的 500ms 轮询、Reconciler 的检测延迟，累积可能导致已成交腿裸奔数秒。在高波动市场中，数百毫秒的暴露窗口就会产生真实亏损。
- **风险**：部分成交触发补偿→补偿失败的路径依赖于人工介入（`NEEDS_MANUAL` 阻塞整个系统），一次失败可能中断数小时的交易能力。

本阶段优化的核心目标：**在不改变架构的前提下，逐项消除时延和风险瓶颈，让每笔交易的执行质量接近同一时间窗口内的最优可能。**

每一项优化都是独立的、可增量交付的。每完成一项，系统就比之前更好一分。

---

## 2. 指导原则

1. **增量叠加，不重构。** 现有 Plan→Validate→Execute→Reconcile pipeline 是正确的设计。所有优化都插在现有阶段的内部或之间，不改变阶段间的接口契约。
2. **每项优化都有可量化的验收标准。** "提升了执行质量"不是目标，"Reconciler 启动延迟从检测到失败 < 100ms（原 < 200ms）"才是。
3. **生产先行，再谈自动化。** 先把手动执行的质量做到极致，再考虑 Agent/自动化入口。
4. **不做策略。** oneFill 仍然是执行器。本阶段的优化只关心"执行质量"，不关心"何时该交易"或"该交易多少"。

---

## 3. 优化层次总览

```
Tier 1 ─ 执行质量 & 风险控制（直接影响单笔盈亏）
  ├── 1a. WebSocket 实时 fill 推送
  ├── 1b. Pre-trade 风控护栏
  ├── 1c. IOC / FOK 订单标志
  └── 1d. Reconciler 启动时间优化

Tier 2 ─ 价格改善 & 场所扩展（改善执行成本）
  ├── 2a. Smart Split 智能分配
  ├── 2b. 新增 Venue（OKX、Bybit 优先）
  └── 2c. Plan 阶段时延优化（quote 短期缓存）

Tier 3 ─ 自动化 & 可观测性（规模化运营）
  ├── 3a. 策略层 Hook（盈利策略的基础）
  ├── 3b. Agent SDK 对接
  ├── 3c. 监控 & 告警
  └── 3d. TWAP / VWAP 执行算法
```

---

## 4. Tier 1 — 执行质量 & 风险控制

### 1a. WebSocket 实时 fill 推送

**当前问题：**

Execute 阶段用 500ms 轮询查询 fill 状态。在两次轮询之间：
- 已有一个 venue 成交但系统不知情 → 检测到一个腿失败 + 另一个腿已成交可能需要 1-3 次轮询周期（500ms-1.5s）
- 在这段时间里，已成交腿是裸头寸，没有任何对冲

**目标方案：**

在执行阶段引入 WebSocket 用户数据流监听：

1. **Binance**：User Data Stream (`listenKey` 机制)
   - `POST /api/v3/userDataStream` 获取 `listenKey`
   - WS 订阅 `wss://stream.binance.com:9443/ws/<listenKey>`
   - 监听 `executionReport` 事件 → 实时更新 leg 状态
   - 每 30 分钟 renew listenKey

2. **Hyperliquid**：WebSocket 订阅
   - `subscribe` 消息包含 `type: "orderUpdates"` 和 `user` 地址
   - 监听 `order` 和 `fill` 事件类型

3. **混合模式**：WS 为主、轮询为后备（WS 断线后降级到轮询）

**实现路径：**

`BaseExchange` 已有抽象方法 `connect_websocket()` 和 `subscribe_orderbook()`。需要新增：
```python
# BaseExchange 新增
async def subscribe_user_data(self) -> None: ...
async def on_user_data(self, callback) -> None: ...
```

`Executor` 改造：
```python
# 当前：poll_loop → asyncio.sleep(500ms) → fetch_order(all legs)
# 改为：
# 1. 并发创建 WS 用户数据订阅 + 启动后台等待 loop
# 2. ws_event_queue 接收 fill/reject/cancel 事件
# 3. 每个事件立即更新对应 leg 的 fill 状态
# 4. 所有 leg 到达终态 → 立即返回，无额外延迟
# 5. 轮询作为 fallback（30s 内没收到 WS 事件 → 降级到 HTTP poll）
```

**验收标准：**

- 单个 leg fill → 更新 leg 状态的延迟 < 100ms（从 fill 事件时间戳到内存状态更新）
- 最后一个 leg 到达终态 → `ExecutionResult` 返回的延迟 < 50ms
- WS 断线 → 自动降级到轮询，日志记录降级事件
- 不改变 Executor 对外的 `ExecutionResult` 接口

**依赖：** 无。Binance 和 Hyperliquid 都已支持 WS 用户数据流，不需要新依赖。

---

### 1b. Pre-trade 风控护栏

**当前问题：**

系统没有任何全局风控约束。一个参数错误或逻辑 bug 可能在单笔订单中发出远超预期的名义价值金额，或多个 Intent 累积大量敞口。

**目标方案：**

在 Validator 阶段新增可选的风控检查层，所有检查在同一阶段并发执行，一条不通过则 REJECTED。

**三类护栏（硬编码默认值 + 可配置覆盖）：**

| 护栏 | 默认值 | 说明 |
|---|---|---|
| 单笔最大 notional | $5,000 | 防止参数错误导致超大单 |
| 日内累计亏损上限 | $1,000 | 跨 Intent 累计，超过则阻塞所有后续 Intent（类似 NEEDS_MANUAL 逻辑） |
| 单 venue 最大暴露 | $10,000 | 防止单个交易所在补偿失败后暴露过大 |

**实现路径：**

1. `PersistenceStore` 新增方法：
```python
async def get_daily_pnl_usd(self) -> float: ...
async def get_venue_exposure_usd(self, venue: str) -> float: ...
```

2. `Validator` 新增三个检查项：
```python
class RiskValidator:
    def __init__(self, store: PersistenceStore):
        self._store = store
        self._max_notional_per_intent = 5_000.0
        self._max_daily_loss = 1_000.0
        self._max_venue_exposure = 10_000.0

    async def check_intent(self, intent: Intent) -> list[RiskFailure]: ...
```

3. `Orchestrator` 在 `submit()` 中，Validate 阶段之后、Execute 之前插入风险检查。风险不通过 → REJECTED（不进入 Execute）。

4. 日内亏损上限的 `NEEDS_MANUAL` 状态复用现有阻塞机制——当天累计亏损超过阈值时，系统进入 `RISK_LIMIT_BLOCKED` 状态（等同于 `ROLLED_BACK_FAILED` 的阻塞效果），需要 `ack` 命令手动解除。

**配置：**

```yaml
# config/risk.yaml（新文件）
risk:
  max_notional_per_intent_usd: 5000
  max_daily_loss_usd: 1000
  max_venue_exposure_usd: 10000
  max_intents_per_minute: 10      # 频率限制，防止误操作狂发
```

**验收标准：**

- 超 notional 限额的 Intent → REJECTED，有明确的 rejection reason
- 交易导致日内累计亏损超过 $1,000 → 系统阻塞（等同于 NEEDS_MANUAL），提示用户执行 `onefill ack`
- 风险配置可通过 `config/risk.yaml` 修改，不需要改代码
- 所有风控检查有结构化日志记录

**依赖：** SQLite 存储日内 PnL 统计。当前 `PersistenceStore` 已有 `audit_events` 表，本项需要新增 daily 聚合查询。

---

### 1c. IOC / FOK 订单标志

**当前问题：**

市价单可能部分成交——当前逻辑下，部分成交直接触发 Reconciler。每次 Reconciler 都是一次额外交易（反向下单有手续费 + 滑点成本），且有不小的概率进入 NEEDS_MANUAL。

**目标方案：**

对市价单支持 IOC（Immediate-or-Cancel）标志。含义：立即以市价成交，剩余未成交部分直接取消，不留在订单簿上等待。

- IOC 市价单 → 消除了"部分成交但剩余量悬而未决"的时间窗口
- 可能的剩余量在毫秒内被取消 → Reconciler 只需要对冲已成交部分（精确知道 fill 数量）

**为何 IOC 比 FOK 更适合当前架构：**

FOK 要求全部成交否则全部取消——在高波动市场中，FOK 几乎永远无法成交（价格在 50ms 时间窗口内就会偏离）。IOC 允许部分成交，且不等待剩余量，更贴近实际市场行为。

**实现路径：**

1. `Intent` 新增字段：
```python
time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
```

2. CLI 新增 `--time-in-force` / `--tif` 参数，默认 `GTC`，可选 `IOC`、`FOK`。

3. `Executor` 在 `create_order` 调用中透传 `timeInForce`：
   - Binance ccxt: `exchange.create_order(symbol, type, side, amount, params={"timeInForce": "IOC"})`
   - Hyperliquid ccxt: 通过 `params` 透传

4. IOC 订单的 `Partial fill → Reconciler` 路径不变，但 Reconciler 能更快拿到精确的已成交数量（因为未成交部分已被交易所 cancel）。

**验收标准：**

- `--tif IOC` 的市价单在 Binance 上返回部分成交 + 剩余 cancel，不触发 Reconciler 的额外等待
- `--tif FOK` 的市价单在不满足全部成交条件时立即 reject，不产生任何部分成交
- 限价单的 GTC/IOC/FOK 行为与交易所标准一致

**依赖：** 无。ccxt 已支持 `timeInForce` 参数，Binance 和 Hyperliquid 的原生 API 都支持。

---

### 1d. Reconciler 启动时间优化

**当前问题：**

Reconciler 的触发依赖于 Executor 的完整返回——而 Executor 需要等所有 leg 到达终态（fill / reject / timeout）才返回。如果 Hyperliquid leg 因网络抖动延迟响应，但 Binance leg 已经在 100ms 内 filled，Reconciler 就开始得晚了。

**目标方案：**

"先到先补"策略：当 Executor 检测到第一个 leg 已 fill + 另一个 leg 已失败或超时时，不等剩余 leg 的最终状态，立即对已 fill 腿启动对冲。

实现分两层：

1. **快速路径（WS 驱动，见 1a）**：WS 推送 leg 失败事件 → 立即检查其他 leg 是否已有 filled 的 → 是 → 立即启动补偿
2. **正常路径（轮询降级）**：每轮 poll 后检查"是否有 leg 已 filled + 是否有 leg 已 failed" → 是 → 启动补偿

这不需要改变 Reconciler 的逻辑，只需要改变 Executor 内部的"何时触发 Reconciler"的决策逻辑。

**验收标准：**

- 单腿 filled + 另一腿 failed → Reconciler 启动 < 100ms（从 failed 事件到第一个对冲订单发出）
- 不影响全部 legs 正常 fill 的路径
- 对冲完成后，剩余还在 pending 的 leg 仍然被取消（清理工作）

**依赖：** 1a（WS 实时 fill 推送）落地后效果最佳，但即使只用 HTTP 轮询也能改善。

---

## 5. Tier 2 — 价格改善 & 场所扩展

### 2a. Smart Split 智能分配

**当前问题：**

用户手动指定 `--split binance=0.5,hyperliquid=0.5`。但在真实市场中，不同所的深度、滑点、手续费差异可能很大。50/50 的分配很可能不是最优的（比如 Binance 深度好但手续费高、Hyperliquid 深度浅但便宜）。

**目标方案：**

当用户不提供 `--split` 时，系统自动计算最优分配比例。

**最简 MVP 方案（不需要复杂优化算法）：**

1. 对每个 venue，Planner 已经拿到了 Quote（含完整 depth）
2. 用简单的启发式评分：
   - 滑点得分 = (1 - 预期滑点%)（滑点低 → 得分高）
   - 手续费得分 = (1 - taker_fee_rate)（费率便宜 → 得分高）
   - 综合得分 = 滑点得分 × 0.7 + 手续费得分 × 0.3（可配置权重）
3. 按得分比例分配 notional，对齐 qty_step

这比数学上的最优解差一些（没有考虑不同分配量下滑点是非线性的），但实施成本极低，且在 Planner 阶段就能完成（不需要额外 API 调用）。

**后续升级（需要更多基础设施时）：**

真正的深度优化——对每个 venue 模拟不同分配量下的 `estimate_fill` 结果，用梯度下降或线性规划求解最优分配。但当前 depth 数据已经在 Quote 里，`estimate_fill` 已经计算了穿透多档的加权均价，循环试几个分配比例即可。

**CLI 变更：**

```bash
# 手动 split（现有行为，不变）
onefill order --split binance=0.5,hyperliquid=0.5 ...

# 自动分配（新）
onefill order --split auto ...
# 或：不传 --split，系统自动在所有可用 venue 上分配
```

**算法细节：**

```
输入: Intent 的 total_notional_usd、参与 venues、每个 venue 的 Quote
输出: dict[venue, ratio]

1. 对 target_amounts = [0.1, 0.2, 0.3, ... 0.9] * total_notional_usd 的每个比例
    对每个 venue 调 estimate_fill(target_amount)
    → 得到 expected_slippage[venue][ratio]
2. 对每个 venue，计算不同比例下的 effective cost：
    cost = expected_slippage + taker_fee_rate
3. 用贪心算法：从 0 开始，每次把 1% notional 分配给当前边际成本最低的 venue
4. 返回最终分配比例
```

**验收标准：**

- `--split auto` 产出一个 Plan，所有 leg 的分配比例由系统计算，总和为 1.0
- 自动分配的加权预期成本 ≤ 用户能实现的任意固定比例
- Plan 输出中展示自动分配的理由（每个 venue 的滑点、费率、综合评分）
- 如果不传 `--split`，系统提示"使用自动分配还是手动指定？"

**依赖：** 无。全部在 Planner 内部完成，不涉及新 API 调用。

---

### 2b. 新增 Venue

**当前状态：**

两个 venue：Binance（现货 + 合约）、Hyperliquid（仅合约）。

**优先级：**

| Venue | 优先级 | 理由 |
|---|---|---|
| **OKX** | P0 | 中文区流动性第二大的 CEX，现货和合约都有，ccxt 原生支持 |
| **Bybit** | P1 | 合约交易量大，积分/空投活跃用户多，ccxt 原生支持 |
| **dYdX** | P2 | 最大的 perp DEX 之一，有独立 SDK（非 ccxt），需要新适配器 |
| **Gate.io** | P3 | 合约和现货都有，利率较低 |

**每加一个 Venue 的工作量：**

对 ccxt 型交易所（OKX、Bybit、Gate.io）：

1. `config/exchanges.yaml` 添加配置块（REST/WS URL、fees、symbols）
2. `config/secrets.yaml` 添加凭证块
3. `CCXTExchange._build_ccxt_config` 添加交易所特定的 options（如网络、auth 方式）
4. 运行 `onefill instruments --refresh` 验证 `list_markets()` 返回的 Instrument 列表正确
5. 发一笔 $10 testnet 订单验证 `create_order` → `fetch_order` 全路径
6. 写 2 个测试（network 级）：`test_okx_testnet_connect.py` + E2E testnet 订单

对非 ccxt 型交易所（dYdX），需要实现新的 `BaseExchange` 子类。工作量约是 ccxt 型的 3-5 倍。

**OKX 特殊说明：**

OKX 的 ccxt 实现成熟。需要注意：
- OKX testnet（demo trading）账号与生产账号完全分离，需要单独注册
- OKX 的 `uid` 字段在 HTTP header 中传递，ccxt 已处理
- OKX 对某些币种有 `maxLeverage` 限制，Planner 需要根据 Instrument 的具体杠杆上限调整

**验收标准（每个新 Venue）：**

- `onefill venues` 显示该所已配置且 enabled
- `onefill instruments --venue <venue>` 返回 100+ 个 trading 状态的 Instrument
- Dry-run 产生正确的 Plan（含 fill 预估、滑点、费率）
- Testnet 真实订单 $10-20 → ALL_FILLED + ALL_FILLED json 输出正确
- 注入失败测试（断网或错误 API URL）→ ROLLED_BACK 路径成功

---

### 2c. Plan 阶段时延优化

**当前问题：**

Plan 阶段对每个 venue 并发调用 `fetch_orderbook()`——在 2 个 venue 时还好，到 4-5 个 venue 时网络往返时间叠加可能达到 500ms-1s。且 Plan 阶段没有副作用，有优化空间。

**目标方案：**

在 `QuoteFetcher` 中引入短期 LRU 缓存：

```python
class QuoteFetcher:
    def __init__(self, exchanges, cache_ttl_ms: int = 200):
        self._cache: dict[str, tuple[float, Quote]] = {}  # symbol → (timestamp, quote)
        self._cache_ttl_s = cache_ttl_ms / 1000
```

- 同一 symbol 在 200ms 内的重复请求直接返回缓存
- 缓存 key 是 `(venue, symbol)`，不同 venue 不共享
- 每次 `fetch` 和 `fetch_many` 先查缓存，miss 才真正调用 exchange

**为什么 200ms：**

200ms 对于现货市场来说足够短（mid price 的波动在 200ms 内通常 < 1bp），但又足够长来覆盖同一个 Intent 内对同一 venue 的不同 quote 查询（如果有的话）。

**验收标准：**

- 连续两次 `fetch(same_instrument)` 在 200ms 内的响应时间 < 1ms（缓存命中）
- `fetch_many` 的缓存命中率在日志中可见
- 缓存过期的 quote 不会返回给 Planner（TTL 过期 → 重新拉取）

**依赖：** 无。纯内存结构。

---

## 6. Tier 3 — 自动化 & 规模化

### 3a. 策略层 Hook

**定位：**

oneFill 不做策略，但应该让策略程序能够无摩擦地调用它。目前 `Orchestrator.submit(intent)` 已经是 Python API，缺少的是：

1. 一个稳定的、版本化的 Python 调用入口（不是通过 CLI 字符串）
2. 策略程序注册回调的能力（订单完成后通知策略，而不是策略轮询）

**实现路径：**

```python
# src/api/__init__.py（新 package）
from src.coordinator.orchestrator import Orchestrator
from src.coordinator.intent import Intent

class OneFillAPI:
    """稳定的 Python API，供策略程序调用。"""

    def __init__(self, orchestrator: Orchestrator):
        self._orch = orchestrator
        self._on_complete: list[callable] = []

    async def submit(self, intent: Intent, dry_run: bool = False) -> dict:
        """提交 Intent，返回结果 dict（与 CLI --json 输出格式一致）。"""
        result = await self._orch.submit(intent, dry_run=dry_run)
        for cb in self._on_complete:
            await cb(result)
        return result

    def on_completion(self, callback: callable) -> None:
        """注册完成回调：async fn(dict) -> None"""
        self._on_complete.append(callback)
```

**为何这很重要：**

策略程序（无论是手动写的套利脚本，还是 Claude Agent）用这个 API 发单后，可以异步收到完成通知，不需要自己轮询状态。这对自动化策略来说是基本要求。

**验收标准：**

- `OneFillAPI.submit(intent)` 返回与 CLI `--json` 格式一致的 dict
- 回调函数在 Orchestrator 返回后异步执行，不阻塞主流程

---

### 3b. Agent SDK 对接

**定位：**

将 oneFill 的 Python API 注册为 Claude Agent SDK 的 tool，用户用自然语言表达交易意图。

**这不是一个新项目，而是 CLI 的另一个入口。** Agent 本质上也是调 `Orchestrator.submit()`，和 CLI、策略脚本、策略程序没有区别。

**核心工作：**

1. 定义 tool schema（Intent 的字段作为 tool 参数）
2. 写 `src/api/agent_tools.py`，把 `OneFillAPI.submit` 包装成 Claude Agent SDK tool 格式
3. 写一个 demo agent 脚本：用户说"帮我在 Binance 和 Hyperliquid 上各用 $50 买 BTC"，Agent 解析参数→调 tool→返回结果

**关键细节：**

- Agent 输入自然语言 → Agent 需要提取 `base`、`side`、`total_notional_usd`、`split` 等参数
- 模糊参数（如"买一点"→需要追问数量）需要在 Agent 侧处理
- Dry-run 模式在 Agent 场景特别有用：Agent 先用 dry-run 确认成本和可行性，再提交真实单

**验收标准：**

- Demo Agent 能从自然语言中正确提取交易参数并构造 Intent
- Agent 调 `submit(intent)` → 返回结果 → Agent 用自然语言向用户汇报
- Dry-run → 确认 → 执行的交互流正常工作

**依赖：** OneFillAPI（3a）作为底层接口。

---

### 3c. 监控 & 告警

**当前问题：**

`NEEDS_MANUAL` 事件发生后，唯一的通知方式是用户主动运行 `onefill recover`。如果用户不在电脑前，可能在数小时内都不知道系统被阻塞了。

**目标方案：**

1. **Metrics 埋点**（PRD §8.3 预留的 `MetricsEmitter` 接口）：
   - Intent 状态计数（`onefill_intents_total{status=ALL_FILLED|REJECTED|ROLLED_BACK|NEEDS_MANUAL}`）
   - Execute 时延分布（`onefill_execute_duration_ms` histogram）
   - 各 venue 错误率（`onefill_venue_errors_total{venue=binance|hyperliquid}`）
   - Reconciler 触发率

2. **NEEDS_MANUAL 告警**：
   - 一旦 Intent 进入 `ROLLED_BACK_FAILED` 状态，触发告警
   - 告警渠道：stdout JSON 日志（基础上）→ Slack webhook（第一阶段）→ Telegram bot（第二阶段）
   - 告警内容：intent_id、涉及 venue、residual_exposure_usd、发生时间

**实现路径：**

```python
# src/observability/metrics.py
class MetricsEmitter:
    def increment(self, name: str, value: int = 1, tags: dict = None): ...
    def histogram(self, name: str, value: float, tags: dict = None): ...

class NoopEmitter(MetricsEmitter): ...

class LogEmitter(MetricsEmitter):
    """将 metrics 编码为结构化日志，后续可被 Loki/Datadog 采集。"""

class AlertSender:
    async def send_needs_manual_alert(self, intent_id: str, exposure: float): ...
```

初始实现只用 `LogEmitter`（成本为零，日志可被任何日志采集系统消费）。Prometheus exporter 作为后续可选项。

**配置：**

```yaml
# config/monitoring.yaml（新文件）
monitoring:
  metrics:
    enabled: true
    emitter: log  # log | prometheus | noop
  alerts:
    needs_manual:
      enabled: true
      slack_webhook_url: ""  # 留空则不发送
```

**验收标准：**

- `NEEDS_MANUAL` 事件发生 → 结构化 JSON 日志立即输出，包含所有关键字段
- `LogEmitter` 在 Orchestrator 的每个阶段转换边上产生计数事件
- 如果配置了 Slack webhook → NEEDS_MANUAL 事件在 5s 内推送 Slack 消息

---

### 3d. TWAP / VWAP 执行算法

**定位：**

大单拆碎、分时间窗口执行。当前"一次性市价单"对大额订单来说冲击成本太高，TWAP/VWAP 把冲击分摊到时间轴上。

**MVP 方案（简化 TWAP）：**

1. 用户指定总 notional、总时间窗口（如 `--twap-window 600` = 10 分钟）、切片数（如 `--twap-slices 10`）
2. 系统在时间窗口内均匀切 N 片，每片按时发出（`total_notional / N` per slice）
3. 每片的订单类型由用户指定（market/limit）
4. 所有已发出切片的 fill 状态在 `intents` 表中追踪（一个 Intent、N 条 leg）

**复杂度来源：**

- 切片之间的间隔需要精确控制（不能用 `asyncio.sleep` 漂移累积）
- 如果某一切片失败，后续切片应该停止（避免累积更大暴露）还是继续执行（已完成部分可能已经是 net profitable 的）
- 跨 venue TWAP 的难度显著高于单 venue——每个切片都是 N 个 venue 的并发订单

**MVP 范围：**

先做单 venue TWAP（`--twap-window` 和 `--twap-slices` 仅在单 venue 场景下支持），跨 venue TWAP 在单 venue 版本稳定后再加。

**验收标准：**

- 指定 `--twap-window 60 --twap-slices 4` 在 60 秒内均匀发出 4 个切片
- 所有切片 fill 后返回 ALL_FILLED，含每个切片的成交明细
- 中途 cancel → 已发出但未 fill 的切片被撤销，已 fill 切片正常记录
- 时间窗口内实际切片间隔偏差 < 5%

**依赖：** 1a（WebSocket fill 监听）—— TWAP 场景下 fill 反馈延迟直接影响下一个切片的执行，所以 WS 是前提。

---

## 7. 时间线建议

| 优化项 | 预估工作量 | 依赖 | 建议顺序 |
|---|---|---|---|
| 1a. WS fill 推送 | 3-5 天 | 无 | **第 1 项** |
| 1b. Pre-trade 风控 | 2-3 天 | 无 | **第 2 项** |
| 1c. IOC/FOK | 1-2 天 | 无 | **第 3 项** |
| 1d. Reconciler 启动优化 | 1-2 天 | 1a（最佳效果） | 第 4 项 |
| 2a. Smart Split | 2-3 天 | 无 | 第 5 项 |
| 2b. 新增 OKX | 2-3 天 | 无 | 第 6 项 |
| 2c. Plan 时延优化 | 0.5-1 天 | 无 | 第 7 项 |
| 2b. 新增 Bybit | 2-3 天 | 无 | 第 8 项 |
| 3a. 策略层 Hook | 1-2 天 | 无 | 第 9 项 |
| 3c. 监控告警 | 2-3 天 | 无 | 第 10 项 |
| 3b. Agent SDK | 3-5 天 | 3a | 第 11 项 |
| 3d. TWAP/VWAP | 5-8 天 | 1a | 最后 1 项 |

**总计：** 约 27-42 天（单人全职），按半职计算约 8-14 周。

Tier 1 的 4 项（约 7-12 天）完成后，系统已经是一个**生产可用的执行器**，具备了实时反馈、风控护栏、和减少部分成交风险的能力。这是第一个里程碑。

---

## 8. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| WS 用户数据流的 listenKey 过期/断线导致 fill 事件丢失 | 混合模式（WS 为主 + HTTP 轮询降级），降级时产生告警日志 |
| Smart Split 在深度数据不准确时产生次优分配 | 允许用户显式传 `--split` 覆盖自动分配 |
| 新增 Venue 时各所的 auth 方式、最小下单量、手续费结构差异大 | 每个新 Venue 先在 testnet 上充分测试（≥50 笔订单）后才开 mainnet |
| TWAP 中因切片间隔过长导致大量 slippage | TWAP 用户必须设置 `--max-slippage`，单切片超阈值 → 暂停执行 |
| 风控护栏阻止了合法的交易意图 | 护栏参数在 `config/risk.yaml` 可配置，用户可以调整阈值 |

**开放问题：**

- [ ] 日内累计亏损的计算口径：是否包含手续费？是否包含未实现亏损（perp 浮动盈亏）？
- [ ] TWAP 场景下部分切片失败的语义：停止整个 TWAP 还是跳过该切片？（建议停止整个，避免"说好的总 notional 却没达到"）
- [ ] Agent SDK 对接时，用户的交易密钥的管理方式：Agent 进程内加载还是 Agent 调 CLI？
- [ ] 多用户/多 key 支持（同一 binance 账户不同 sub-account）何时需要？
