---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: onefill CLI users
---

# User Guide

本目录只描述当前用户可以运行和配置的能力。内部架构、源码扩展和设计说明请阅读[开发者指南](../developer-guide/index.md)。

## 目录结构

| 目录 | 内容 |
|---|---|
| `getting_started/` | 安装、凭据、风险配置和第一次 dry-run |
| `cli/` | 全部命令、参数、输出和退出码 |
| `configuration/` | 交易所、凭据、风险和策略配置，以及预交易限制与人工恢复流程 |
| `examples/` | 按功能分组的可运行命令示例 |
| `api/` | 可从外部调用的接口及状态判定方式 |

## 页面

| 页面 | 作用 |
|---|---|
| [快速开始](getting_started/quickstart.md) | 安装、配置并跑通第一次 dry-run |
| [CLI Reference](cli/index.md) | 当前全部命令、参数、输出和退出码 |
| [配置](configuration/index.md) | 交易所、凭据、风险和策略配置文件 |
| [交易所凭据](configuration/credentials.md) | Arcus、Hyperliquid、Binance 的凭据获取、配置和测试网验证 |
| [风险控制](configuration/risk-controls.md) | 订单发送前的限制和人工恢复流程 |
| [示例](examples/index.md) | 各功能的可运行示例 |
| [DEX 测试网验证](examples/dex-testnet-validation.md) | 默认只读、显式限额交易、WS 与独立数据库恢复证据 |
| [API](api/index.md) | `submit_intent_from_dict` 的用法与状态判定 |

## 核心概念

- `Intent`：一次完整的交易目标。
- `Leg`：一个 Intent 在单个交易所上的执行单元。
- `Plan`：执行前的标的、价格、滑点和费用估算。
- `ROLLED_BACK_FAILED`：补偿失败后的阻断状态，用户界面也称为 `NEEDS_MANUAL`。
