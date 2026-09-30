---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-30
applies_to: documentation entry point
---

# Omnitrade 文档

Omnitrade（CLI 名称 `onefill`）是一个多交易所协调执行引擎，同时提供资金费率套利、价格监控、回测和交易台账能力。

## 从这里开始

| 读者 | 入口 | 内容 |
|---|---|---|
| 使用者 | [用户指南](user-guide/index.md) | 安装、配置、命令、风险控制和故障处理 |
| 开发者 / AI | [开发者指南](developer-guide/index.md) | 架构、状态、不变量、扩展方式、设计和 API |
| 所有贡献者 | [docs-paradigm.md](docs-paradigm.md) | 文档目录、权威级别、生命周期和写作约束 |

## 当前边界

- 执行内核负责把一个 `Intent` 拆成多条 `Leg`，并保证协调终局。
- 策略模块决定是否交易以及交易规模；策略通过执行内核发起订单。
- 支持的产品类型由源码定义，目前为 `spot` 和 `perp`。
- 当前文档只描述已确认的实现。未实现方案必须在开发者指南中标记为 `proposal`。

## 运行前提醒

交易所连接、凭据和风险配置会直接影响真实资金。请先使用 testnet 或 demo 环境，并阅读[风险控制](user-guide/configuration/risk-controls.md)。
