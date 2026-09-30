---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
applies_to: Omnitrade execution engine and current strategy consumers
---

# 产品与领域约束

本文档描述当前实现必须遵守的产品边界。它不是历史重构计划，也不描述尚未实现的产品愿景。

## 产品定位

Omnitrade 的 CLI 命令为 `onefill`；`oneFill` 是其中的协调执行功能。Omnitrade 是一个多交易所协调执行引擎：调用方提交已经决定好的交易意图，系统负责选择交易标的、校验、并发下单和失败回滚。

策略可以决定“是否交易”和“交易多少”，但协调器不负责生成交易策略。策略和 Agent 都必须通过同一条 Intent 提交路径进入执行内核。

## 唯一领域概念

| 概念 | 定义 |
|---|---|
| `Asset` | 用户侧的基础资产标识，例如 `BTC`；不绑定交易所或计价资产 |
| `Instrument` | 可交易的最小市场单元，由交易所、产品类型、基础资产和计价资产共同确定 |
| `Intent` | 一次完整的用户交易目标，包含总名义金额、方向、产品默认值和交易所拆分 |
| `Leg` | 一个 Intent 在单个交易所上的执行单元 |
| `Plan` | Planner 根据 Instrument、Quote 和阈值得出的执行计划 |
| `LegProtection` | 每腿固定参考价和可接受执行边界；重新报价不得放宽边界 |
| `OrderRequest` / `OrderSnapshot` | 交易所层的规范化订单请求和累计成交快照；一条 Leg 可有多笔顺序原单和补偿单 |
| `NEEDS_MANUAL` | 面向用户的描述；源码中的阻断状态名称是 `ROLLED_BACK_FAILED` |

代码中的类名、字段名和状态名优先于本表中的自然语言描述。

### 概念之间的归属关系

<figure markdown="span">
  <img src="../../../assets/sys-product-requirements.svg" alt="sys-product-requirements" width="100%">
</figure>

（图源码 `docs/assets/sys-product-requirements.dot`，重新生成：`scripts/render_diagrams.sh sys-product-requirements`）

**`Intent` → `Leg` 是一对多，`LegConfig` 是那条一对多关系上的覆盖层。** 这个结构使一个 Intent
可以跨 venue 混用 spot/perp、buy/sell 和不同杠杆——而 `Intent.product` / `side` / `leverage`
**始终是默认值，不是约束**。Spot 腿的 `leverage` 必须为 `1`，由 `Intent.__post_init__` 强制。

`Asset` 与 `Instrument` 的分工是这条链上最容易搞错的一处：用户说 `BTC`（`Asset`），
系统选出 `BTC/USDT` perp @ binance 与 `BTC/USDC` spot @ hyperliquid（两个 `Instrument`）。
**venue 原生 symbol 只活在 `Instrument` 里**，`Intent` 及更高层从来看不见它。

## 产品类型和逐腿覆盖

当前支持的产品类型为 `spot` 和 `perp`。`Intent` 的 `product`、`side`、`leverage`、`contract_type` 和 `settlement_asset` 是默认值；每条 `Leg` 可以通过 `LegConfig` 覆盖。永续默认 `linear`，币本位需显式选择 `inverse`。Binance 仅支持普通账户、单向持仓、单资产保证金；不支持交割、组合保证金和币本位跨所套利。

`position_effect` 显式区分 `open` / `close`，默认开仓。`close_all` 仅允许单腿永续平仓，此时可省略金额；`quantity_native` 指定单腿原生数量，不能与 `close_all` 同时使用，金额仍作为预算。平仓只减少现有持仓，失败时不能重新开仓。

因此，一个 Intent 可以同时包含 spot/perp 或 buy/sell 方向不同的腿，这是资金费率套利和跨市场对冲所必需的行为。spot 腿的 leverage 必须为 `1`。

交易所原生 symbol 只能在市场层和交易所适配层处理；Coordinator、策略和 CLI 使用 `Instrument`、`base`、`quote_preference` 等跨交易所概念。

## 执行顺序

`Orchestrator.submit()` 的确定顺序是：

1. 检查系统是否被 `ROLLED_BACK_FAILED` 阻断。
2. 持久化 `PENDING` Intent。
3. Planner 选择 Instrument 并估算成交、滑点、费用和 funding。
4. Validator 执行交易所和账户预检。
5. RiskValidator 执行名义金额、亏损、敞口和速率限制检查。
6. 持久化 `VALIDATED`，由 Executor 并发发单并跟踪成交。
7. 全部成交进入 `ALL_FILLED`；开仓部分成交进入 Reconciler 恢复基线，显式平仓失败直接阻断，不重开仓。

Planner 和 Validator 不得产生交易副作用。Executor 和 Reconciler 是唯一允许发单的协调阶段。

## Intent 终态

当前终态为：

- `ALL_FILLED`：所有腿按计划完成。
- `DRY_RUN`：预览检查结束，未发送订单。
- `REJECTED`：计划、验证或风险检查失败，未产生有效执行。
- `ROLLED_BACK`：发生部分执行，补偿成功，净敞口恢复到目标范围。
- `ROLLED_BACK_FAILED`：补偿失败，系统阻断后续 Intent，等待人工处理。
- `RESOLVED_MANUAL`：人工确认并通过 `onefill ack` 解除阻断。

任何新增状态都必须先修改状态机实现、测试和本文档，不能只在文档中创造名称。

## 持久化和安全边界

- 每次 `create_order` 调用前必须先持久化对应的 `Leg` 行。
- 每笔原单与补偿单还必须持久化独立的 OrderRow；客户端 ID 稳定，不因重试重新生成。
- `UNKNOWN` 是 Leg 的查询状态，不是拒单；恢复失败进入 `ROLLED_BACK_FAILED`。
- SQLite 保存当前状态，JSONL 保存追加式审计事件。
- `ROLLED_BACK_FAILED` 不允许自动重试或自动清除，必须人工确认。
- 风险配置来自 `config/risk.yaml`，示例必须保留顶层 `risk` 节点。
- 交易所凭据只存在于 Git ignored 的 `config/secrets.testnet.yaml` / `config/secrets.mainnet.yaml`，
  公共凭据存放于 `config/secrets.yaml`；不得写入文档、日志或测试样例。网络切换同时选择端点和凭据，不跨网络回退。

## 职责边界：不负责什么

上面各节说的是「Intent 能表达什么」。这一节说的是**Omnitrade 整体不做什么**——
它划定的边界比功能列表更能决定一次改动该不该落在这里。

| 不负责 | 归谁 | 为什么 |
|---|---|---|
| 决定**是否**交易、交易多少 | 用户 / Agent / `strategy/` 的信号 | Omnitrade 的 oneFill 功能是执行工具，不是策略工具——这两个判断发生在 Intent 构造之前 |
| 预测价格、择时 | **没有任何组件** | 系统不做方向性下注；它把已决定的意图更快、更完整地执行出来 |
| 保证盈利 | **没有任何组件** | 它保证的是**协调终局**（全部成交、已补偿、或阻断），与盈亏无关 |
| 从 `ROLLED_BACK_FAILED` 自动恢复 | 人工，经 `onefill ack` | 自动补偿本身失败了，自动再试只会掩盖问题；升级给人是设计 |
| 把信号变成订单 | 人 | `price_watch` 只推告警；信号从未经过 Validator / RiskValidator |
| 记录策略回测结果到台账 | 人，显式记一笔 | 台账记的是真实决策，不是执行引擎的副产品 |
| 跨 venue 的净额结算 | 各 venue 各自结算 | Omnitrade 的 oneFill 功能压平的是**自己的净敞口**，不触碰 venue 之间的清算 |

**「保证协调终局」是产品承诺，不是实现细节。** 用户提交一个 Intent 后，系统必然把它推到
`TERMINAL_STATES` 中的某一个：要么全部成交，要么把已成交的部分反向压回，
要么进入 `ROLLED_BACK_FAILED` 并阻断后续 Intent——**不会有「部分成交然后没人管」的第四种结局**。
这条承诺是全部关键不变量的来源（见[系统架构](sys-architecture.md) §6）。

## 非目标

当前产品不承诺以下能力：自然语言 Agent 产品化注册、统一 HTTP API、尚未完成验收的 native
交易所高级能力、自动策略生成，以及未在源码中存在的监控配置系统。Arcus REST、WS 重连、
序列断档恢复、`userFills` 和账户仓位校验已有实现；实现存在不等于生产环境验收通过。
Hyperliquid 统一账户等非单资产模式仍不属于普通 Coordinator 永续执行范围。
[DEX 测试网验证](../../user-guide/examples/dex-testnet-validation.md)覆盖的低层交易路径，
不能外推为普通 Intent 对全部账户模式、产品与高级订单类型的支持。
