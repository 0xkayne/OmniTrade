---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-11
applies_to: src/, config/, tests/ and AI-assisted development
---

# Developer Guide

这里是开发者和 AI 修改 OmniTrade 的主入口。先阅读 [docs-paradigm](../docs-paradigm.md) 和
[代码目录结构规范](standards/directory-structure.md)，然后按下面的层级定位你要改的模块，
而不是把每个页面当作互相独立的说明。

## 目录结构

| 目录 | 内容 |
|---|---|
| `design/` | 功能模块与架构的设计文档；文件名格式 `<域>-<主题>.md`，**域前缀就是架构分层** |
| `standards/` | 开发规范：代码结构、命名、测试组织与验证方式 |
| `reference/` | 参考文档：源码公开 API 和当前实现状态 |
| `harness/` | AI 开发流程：三个阶段 skill、meta-skills 范本与验证门禁 |

## 架构分层

依赖方向自下而上——上层消费下层，下层不知道上层。`design/` 的文件名前缀与这四层一一对应，
所以按文件名排序时同层文档自然聚在一起。

```text
       ┌────────────────────────────────────────────────────────────┐
sys-   │ 全系统（跨层，不属于任何单层）                                │
       │   系统架构与工作流 · 产品与领域约束                              │
       └────────────────────────────────────────────────────────────┘

   ▲   entry-  外部入口
   │             CLI 装配 / Agent API 边界
   │
   │   strat-  策略层 —— 决定「要不要做、做多少」
   │             框架 · 资金费率套利 · 价格监控 · 回测 · 交易台账
   │
   │             ↓ 构造 Intent，交给执行内核
   │
   │   base-   执行内核与基础层
   │             协调流程 · 状态机 · 市场层 · 交易所层 · 持久化层
   │
   依赖方向：上层消费下层
```

## 页面之间的关联逻辑

一次用户请求从外部入口进入系统后，先由 CLI 或程序化 Agent 接口把输入规范化为统一的
`Intent`。Intent 携带基础资产、产品类型、方向、名义金额和交易所拆分，但不携带交易所原生 symbol。

`Orchestrator` 接收 Intent 后调用 `Planner`。Planner 依赖[市场层](design/base-market-layer.md)
的 `InstrumentRegistry` 找到每个 venue 对应的 `Instrument`，再由 `QuoteFetcher` 获取行情并生成 `Plan`。
因此市场层解决「在哪里、以什么市场交易」，不决定「是否应该交易」。

Plan 进入[协调流程](design/base-coordination-pipeline.md)后，依次经过 Validator、RiskValidator、
Executor 和 Reconciler。它们共同遵守[产品与领域约束](design/sys-product-requirements.md)和
[状态机](design/base-state-machine.md)：先完成预检和风险判断，再持久化 Leg 后发单；
部分成交时执行补偿，补偿失败进入阻断状态。

协调流程通过[交易所层](design/base-exchange-layer.md)访问 Binance、Hyperliquid 等 venue，
通过[持久化层](design/base-persistence-layer.md)记录 Intent、Leg、审计和策略数据。
交易所层只负责外部 API 适配，持久化层只负责状态和数据保存，两者都不应重新定义领域概念。

`src/strategy/` 是执行内核的**上游消费者**：它产生信号或构造 Intent，真实执行仍回到 Coordinator。
[策略框架](design/strat-framework.md)是所有策略共用的抽象与数据设施（含 K 线服务与多周期上下文），
在此之上并列四个功能域：[资金费率套利](design/strat-funding-arb.md)、
[价格监控](design/strat-price-watch.md)、[回测](design/strat-backtest.md)、
[交易台账](design/strat-trade-log.md)。
[Agent 接口](design/entry-agent-api.md)与 [API Reference](reference/api/index.md)分别说明
程序化入口和源码公开接口。

最后，测试验证各层的契约：市场和协调器使用 `MockExchange` 做离线测试，Executor/Reconciler 验证副作用
和不变量，网络测试只验证真实 venue 连接。测试放哪、用哪种骨架见[代码目录结构规范](standards/directory-structure.md) §8。
新增模块必须同时落在这条链路中的一个明确位置，并补齐对应测试和文档。

## `design/` — 设计文档

### `sys-` 全系统

| 页面 | 作用 |
|---|---|
| [系统架构与工作流](design/sys-architecture.md) | 当前系统边界、分层、数据落盘映射和全部核心工作流 |
| [产品与领域约束](design/sys-product-requirements.md) | 产品边界、Intent/Leg、产品类型、逐腿覆盖和终态 |

### `base-` 执行内核与基础层

| 页面 | 作用 |
|---|---|
| [协调流程](design/base-coordination-pipeline.md) | Planner、Validator、RiskValidator、Executor、Reconciler |
| [状态机](design/base-state-machine.md) | Intent 与 Leg 状态及合法转移 |
| [市场层](design/base-market-layer.md) | Asset、Instrument、Quote 和 InstrumentRegistry |
| [交易所层](design/base-exchange-layer.md) | BaseExchange、CCXTExchange、MockExchange 和工厂 |
| [交易所接入](design/base-exchange-integration.md) | 新增交易所的实现步骤和验证清单 |
| [持久化层](design/base-persistence-layer.md) | SQLite 表结构、JSONL 审计和落盘顺序 |

### `strat-` 策略层

| 页面 | 作用 |
|---|---|
| [策略框架与共享设施](design/strat-framework.md) | Strategy/Bar/Signal、注册表、pair_band、K 线服务、MTF |
| [资金费率套利](design/strat-funding-arb.md) | 模型推导（为什么是 premium 均值回归）+ 扫描/决策/持仓实现 |
| [价格监控](design/strat-price-watch.md) | watch 守护进程、窗口信号、Telegram 告警与 `/log` |
| [回测](design/strat-backtest.md) | 数据加载、无未来函数的信号引擎、组合与指标 |
| [交易台账](design/strat-trade-log.md) | 手工交易流水、两个写入方与导出格式 |

### `entry-` 外部入口

| 页面 | 作用 |
|---|---|
| [Agent 接口](design/entry-agent-api.md) | 结构化 Intent 的程序化提交入口 |

## `standards/` — 开发规范

| 页面 | 作用 |
|---|---|
| [编码规范](standards/code-standards.md) | 语言风格外包给 Google；本文只写本项目追加的四条原则 |
| [代码目录结构规范](standards/directory-structure.md) | 目标目录层级、分层与依赖方向、各包收录规则、迁移顺序 |
| [命名规范](standards/naming-conventions.md) | 大小写与单位后缀、模块词根所有权、角色后缀、易混名对照 |

## `reference/` — 参考文档

| 页面 | 作用 |
|---|---|
| [API Reference](reference/api/index.md) | 从源码 docstring 生成的公开 Python API |
| [当前状态](reference/current-status.md) | 已实现能力和最近验证结果 |

## 按任务阅读

| 任务 | 阅读顺序 |
|---|---|
| 修改订单执行或失败回滚 | 产品与领域约束 → 状态机 → 系统架构 §6（关键不变量）→ 协调流程 → 持久化层 → 对应测试 |
| 新增或修改交易所 | 编码规范 → 市场层 → 交易所层 → 交易所接入 → API Reference → 网络测试 |
| 新增策略、信号或回测 | 产品与领域约束 → 策略框架 → 对应功能域（套利/监控/回测）→ Coordinator/Intent → 测试 |
| 修改配置或 CLI | 用户配置文档 → CLI Reference → `src/cli/bootstrap.py` / `src/cli/main.py` → 测试 |
| 修改数据库或审计 | 持久化层 → 状态机 → 编码规范 → Persistence tests |
| 只需要查看公开接口 | API Reference → 对应源码 docstring → 对应测试 |

## `harness/` — AI 开发流程

| 页面 | 作用 |
|---|---|
| [Harness](harness/index.md) | 三个阶段 skill（plan / implement / docs-sync）、验证门禁，以及机器能保证与不能保证的分界 |

Skill 正文存放在 `harness/skills/<name>/SKILL.md`，`.claude/skills/<name>` 是指向它们的
符号链接，按需加载；该页面是它们的索引与设计理由。新增 skill 时同步更新上表。

`harness/meta-skills/` 是**范本**，不是本项目产物：它描述「把一套 AI 开发体系建起来」的方法
（认知层 / 执行层 / 生成层 / 演化层），本项目按它建设，但范本正文不描述 oneFill 的产品行为。

## AI 修改顺序

这套顺序已经落成三个按开发阶段加载的 skill，下面列出它们共同覆盖的要点，
细节见 [Harness](harness/index.md)。

1. 阅读本文档、[docs-paradigm](../docs-paradigm.md)和[代码目录结构规范](standards/directory-structure.md)。
2. 确认任务对应的源码包、现有领域概念和测试目录。
3. 复用已有术语、状态、配置键和模块边界。
4. 代码、测试、docstring 和当前文档一起更新。
5. 如果旧文档核心前提失效，直接删除并重写。
