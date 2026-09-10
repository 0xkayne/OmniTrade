---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-06
applies_to: repository state verified on 2026-09-06
---

# 当前实现状态

本文档是当前代码状态的简短快照，不是开发计划。功能变更后应重新核对源码和测试，并直接更新本文件；旧快照不保留为规范。

## 已实现入口

`onefill` 当前包含以下顶层命令：

- 核心执行：`order`、`query`、`list-intents`、`cancel`、`ack`、`recover`、`venues`、`instruments`
- 资金费率套利：`arb scan`、`arb run`、`arb positions`、`arb history`
- 价格监控：`watch run`、`watch backfill`
- 交易台账：`trades record`、`trades list`、`trades export`
- 回测：`backtest run`

命令参数和退出码以 [CLI Reference](api/cli.md) 与 `src/cli/main.py` 为准。

## 已实现模块

- `src/coordinator/`：Intent 规划、校验、风险校验、并发执行和失败回滚。
- `src/market/`：Asset、Instrument、Quote、InstrumentRegistry 和行情获取。
- `src/exchanges/` 与 `src/core/base_exchange.py`：交易所抽象、CCXT 适配和测试替身。
- `src/persistence/`：SQLite 状态、JSONL 审计以及策略数据表。
- `src/strategy/`：资金费率套利、价格监控、K 线/多周期上下文、回测和交易台账。
- `src/observability/`：指标接口和结构化日志支持。
- `src/legacy/` 与 `src/core/`：legacy TradeBot 兼容入口。

## 当前验证结果

截至本文件更新时间，测试收集数为 448；排除网络测试后 437 项通过，11 项标记为网络测试。网络测试需要外部交易所凭据和可用环境，不能作为默认本地验证条件。

默认验证命令：

```bash
uv run --locked pytest -m "not network"
uv run --locked --group docs mkdocs build --strict --site-dir /share/wangziping/tmp/omnitrade-mkdocs-site
```

## 不属于当前实现

未在源码、配置和测试中同时得到确认的未来 HTTP API、原生交易所适配器、监控配置和 Agent SDK 产品化集成，不属于当前实现文档；在获得明确方案和验收标准前，不得写成已完成能力。
