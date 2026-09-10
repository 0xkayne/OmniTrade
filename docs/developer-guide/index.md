---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/, config/, tests/ and AI-assisted development
---

# Developer Guide

这里是开发者和 AI 修改 OmniTrade 的主入口。先阅读 [docs-paradigm](../docs-paradigm.md) 和[代码结构与命名规范](standards/code-standards.md)，然后根据任务沿着下面的关系图阅读，而不是把每个页面当作互相独立的说明。

## 目录结构

| 目录 | 内容 |
|---|---|
| `design/` | 功能模块与架构的设计文档：系统边界、领域约束、各层设计和接入方式 |
| `standards/` | 开发规范：代码结构与命名、测试组织与验证方式 |
| `reference/` | 参考文档：源码公开 API 和当前实现状态 |
| `llm-harness/` | 预留目录：项目开发使用的 skill、MCP、plugin、AGENTS.md 等（当前为空） |

本文档是上述四个目录的共同索引和阅读路径。

## 页面之间的关联逻辑

一次用户请求从外部入口进入系统后，先由 CLI 或程序化 Agent 接口把输入规范化为统一的 `Intent`。Intent 携带基础资产、产品类型、方向、名义金额和交易所拆分，但不携带交易所原生 symbol。

`Orchestrator` 接收 Intent 后调用 `Planner`。Planner 依赖[市场层](design/market-layer.md)的 `InstrumentRegistry` 找到每个 venue 对应的 `Instrument`，再由 `QuoteFetcher` 获取行情并生成 `Plan`。因此，市场层解决“在哪里、以什么市场交易”，不会决定“是否应该交易”。

Plan 进入[协调流程](design/coordination-pipeline.md)后，依次经过 Validator、RiskValidator、Executor 和 Reconciler。它们共同遵守[产品与领域约束](design/product-requirements.md)、[状态机](design/state-machine.md)和[关键不变量](design/invariants.md)：先完成预检和风险判断，再持久化 Leg 后发单；部分成交时执行补偿，补偿失败进入阻断状态。

协调流程通过[交易所层](design/exchange-layer.md)访问 Binance、Hyperliquid 等 venue，通过[持久化层](design/persistence-layer.md)记录 Intent、Leg、审计和策略数据。交易所层只负责外部 API 适配，持久化层只负责状态和数据保存，两者都不应重新定义领域概念。

`src/strategy/` 中的策略是执行内核的上游消费者：资金费率套利、价格监控和回测负责产生信号或构造 Intent，真实执行仍回到 Coordinator；[资金费率套利](design/funding-arbitrage.md)、[Agent 接口](design/agent-integration.md)和 [API Reference](reference/api/index.md)分别说明策略模型、程序化入口和源码公开接口。[Legacy 模式](design/legacy-mode.md)是独立兼容边界，不参与新架构的术语和目录扩展。

最后，[测试](standards/testing.md)验证各层的契约：市场和协调器使用 `MockExchange` 做离线测试，Executor/Reconciler 验证副作用和不变量，网络测试只验证真实 venue 连接。新增模块必须同时落在这条链路中的一个明确位置，并补齐对应测试和文档。

## 按任务阅读

| 任务 | 阅读顺序 |
|---|---|
| 修改订单执行或失败回滚 | 产品与领域约束 → 状态机 → 关键不变量 → 协调流程 → 持久化层 → 对应测试 |
| 新增或修改交易所 | 代码结构与命名规范 → 市场层 → 交易所层 → 交易所接入 → API Reference → 网络测试 |
| 新增策略、信号或回测 | 产品与领域约束 → 系统架构 → 资金费率套利 → Coordinator/Intent → 测试 |
| 修改配置或 CLI | 用户配置文档 → CLI Reference → `src/cli/bootstrap.py` / `main.py` → 测试 |
| 修改数据库或审计 | 持久化层 → 状态机 → 代码结构与命名规范 → Persistence tests |
| 只需要查看公开接口 | API Reference → 对应源码 docstring → 对应测试 |

## design/ — 设计文档

| 页面 | 作用 |
|---|---|
| [系统架构](design/architecture.md) | 当前系统边界、模块关系和完整数据流 |
| [产品与领域约束](design/product-requirements.md) | 当前产品边界、Intent/Leg、产品类型和终态 |
| [状态机](design/state-machine.md) | Intent 与 Leg 状态及合法转移 |
| [关键不变量](design/invariants.md) | 下单、回滚、市场抽象和兼容性约束 |
| [协调流程](design/coordination-pipeline.md) | Planner、Validator、RiskValidator、Executor、Reconciler |
| [市场层](design/market-layer.md) | Asset、Instrument、Quote 和 InstrumentRegistry |
| [持久化层](design/persistence-layer.md) | SQLite、JSONL、表结构和落盘顺序 |
| [交易所层](design/exchange-layer.md) | BaseExchange、CCXTExchange、MockExchange 和工厂 |
| [交易所接入](design/exchange-integration.md) | 新增交易所的实现步骤和验证清单 |
| [资金费率套利](design/funding-arbitrage.md) | 当前策略的模型和实现边界 |
| [Agent 接口](design/agent-integration.md) | 结构化 Intent 的程序化提交入口 |
| [Legacy 模式](design/legacy-mode.md) | 旧入口与新内核的兼容边界 |

## standards/ — 开发规范

| 页面 | 作用 |
|---|---|
| [代码结构与命名规范](standards/code-standards.md) | 目录、文件、类型、变量和依赖方向规范 |
| [测试](standards/testing.md) | 测试分层、MockExchange 和本地验证 |

## reference/ — 参考文档

| 页面 | 作用 |
|---|---|
| [API Reference](reference/api/index.md) | 从源码 docstring 生成的公开 Python API |
| [当前状态](reference/current-status.md) | 已实现能力和最近验证结果 |

## llm-harness/ — 预留

该目录存放项目开发可用的 skill、MCP、plugin、AGENTS.md 等文档，当前为空。新增内容时遵循
[docs-paradigm](../docs-paradigm.md) 的元数据要求，并同步 MkDocs 导航。

## AI 修改顺序

1. 阅读本文档、[docs-paradigm](../docs-paradigm.md)和[代码结构与命名规范](standards/code-standards.md)。
2. 确认任务对应的源码包、现有领域概念和测试目录。
3. 复用已有术语、状态、配置键和模块边界。
4. 代码、测试、docstring 和当前文档一起更新。
5. 如果旧文档核心前提失效，直接删除并重写。
