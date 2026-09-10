---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-06
applies_to: onefill execution engine and current strategy consumers
---

# 产品与领域约束

本文档描述当前实现必须遵守的产品边界。它不是历史重构计划，也不描述尚未实现的产品愿景。

## 产品定位

OmniTrade 的 CLI 产品名为 `onefill`。它是一个多交易所协调执行引擎：调用方提交已经决定好的交易意图，系统负责选择交易标的、校验、并发下单和失败回滚。

策略可以决定“是否交易”和“交易多少”，但协调器不负责生成交易策略。策略和 Agent 都必须通过同一条 Intent 提交路径进入执行内核。

## 唯一领域概念

| 概念 | 定义 |
|---|---|
| `Asset` | 用户侧的基础资产标识，例如 `BTC`；不绑定交易所或计价资产 |
| `Instrument` | 可交易的最小市场单元，由交易所、产品类型、基础资产和计价资产共同确定 |
| `Intent` | 一次完整的用户交易目标，包含总名义金额、方向、产品默认值和交易所拆分 |
| `Leg` | 一个 Intent 在单个交易所上的执行单元 |
| `Plan` | Planner 根据 Instrument、Quote 和阈值得出的执行计划 |
| `NEEDS_MANUAL` | 面向用户的描述；源码中的阻断状态名称是 `ROLLED_BACK_FAILED` |

代码中的类名、字段名和状态名优先于本表中的自然语言描述。

## 产品类型和逐腿覆盖

当前支持的产品类型只有 `spot` 和 `perp`。`Intent` 的 `product`、`side` 和 `leverage` 是默认值；每条 `Leg` 可以通过 `LegConfig` 覆盖这些值。

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
7. 全部成交进入 `ALL_FILLED`；部分成交进入 Reconciler 反向补偿。

Planner 和 Validator 不得产生交易副作用。Executor 和 Reconciler 是唯一允许发单的协调阶段。

## Intent 终态

当前终态为：

- `ALL_FILLED`：所有腿按计划完成。
- `REJECTED`：计划、验证或风险检查失败，未产生有效执行。
- `ROLLED_BACK`：发生部分执行，补偿成功，净敞口恢复到目标范围。
- `ROLLED_BACK_FAILED`：补偿失败，系统阻断后续 Intent，等待人工处理。
- `RESOLVED_MANUAL`：人工确认并通过 `onefill ack` 解除阻断。

任何新增状态都必须先修改状态机实现、测试和本文档，不能只在文档中创造名称。

## 持久化和安全边界

- 每次 `create_order` 调用前必须先持久化对应的 `Leg` 行。
- SQLite 保存当前状态，JSONL 保存追加式审计事件。
- `ROLLED_BACK_FAILED` 不允许自动重试或自动清除，必须人工确认。
- 风险配置来自 `config/risk.yaml`，示例必须保留顶层 `risk` 节点。
- 凭据只存在于 `config/secrets.yaml`，不得写入文档、日志或测试样例。

## 非目标

当前产品不承诺以下能力：自然语言 Agent 产品化注册、统一 HTTP API、未实现的原生交易所适配器、自动策略生成，以及未在源码中存在的监控配置系统。这些内容只能在开发者指南的提案文档中说明。

