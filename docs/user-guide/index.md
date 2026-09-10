---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-06
applies_to: onefill CLI users
---

# User Guide

本目录只描述当前用户可以运行和配置的能力。内部架构、源码扩展和 Agent 接口请阅读[开发者指南](../developer-guide/index.md)。

## 页面

- [快速开始](quickstart.md)：安装、凭据、风险配置和第一次 dry-run。
- [CLI Reference](cli-reference.md)：当前全部命令、参数、输出和退出码。
- [配置](configuration.md)：交易所、凭据、风险和策略配置文件。
- [风险控制](risk-controls.md)：订单发送前的限制和人工恢复流程。

## 核心概念

- `Intent`：一次完整的交易目标。
- `Leg`：一个 Intent 在单个交易所上的执行单元。
- `Plan`：执行前的标的、价格、滑点和费用估算。
- `ROLLED_BACK_FAILED`：补偿失败后的阻断状态，用户界面也称为 `NEEDS_MANUAL`。
