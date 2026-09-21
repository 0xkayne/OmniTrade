---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: src/, tests/, config/ and onefill CLI
---

# 当前实现状态

本文档是当前代码状态的简短快照，不是开发计划。功能变更后应重新核对源码和测试，并直接更新本文件；旧快照不保留为规范。

## 已实现入口

`onefill` 当前包含以下顶层命令：

- 核心执行：`order`、`query`、`list-intents`、`cancel`、`ack`、`recover`、`venues`、`instruments`
- 资金费率套利：`arb scan`、`arb run`、`arb positions`、`arb history`、`arb testnet-smoke`、`arb testnet-canary`
- 价格监控：`watch run`、`watch backfill`
- 交易台账：`trades record`、`trades list`、`trades export`
- 回测：`backtest run`

命令参数和退出码以 [CLI Reference](api/cli.md) 与 `src/cli/main.py` 为准。

## 已实现模块

目录层级与依赖方向见 [代码目录结构规范](../standards/directory-structure.md)。

- `src/cli/`：Typer 命令、`bootstrap` 装配和程序化 Intent 入口；`arb testnet-smoke` 支持 Arcus、Hyperliquid、Binance 的只读测试网市场/盘口检查和可选账户查询；`arb testnet-canary` 受确认串、凭据、IOC、client ID 和名义金额门控，只运行一轮开仓后平仓。
- `src/coordinator/`：Intent 规划、校验、风险校验、固定价格保护、顺序拆单、幂等订单确认和失败回滚；显式恢复中断 Intent。
- `src/market/`：Asset、Instrument、NetworkType、Quote、InstrumentRegistry 和行情获取。
- `src/arbitrage/`：Arcus、Hyperliquid、Binance 三家之间的永续合约配对归一化、深度 VWAP、净价差评估、并发机会扫描、机会级风控、周期状态机、离线模拟、测试网门控的 `HedgedExecutor`、单周期 `TestnetCanary` 和重启恢复决策。默认仍为 dry-run；主网执行被拒绝。
- `src/exchange/`：BaseExchange 抽象、CCXT 适配、Arcus native REST/WS 适配、`ExchangeFactory`、订单簿缓存和测试替身。Arcus 已支持频道隔离、盘口序列断档重订阅、WS 重连、`orders`/`userFills` 归一化和 client ID 查询；真实测试网双腿订单仍需带凭据的人工触发验证。
- `src/persistence/`：SQLite 状态、JSONL 审计以及策略数据表；只读写行，不构造领域对象。
- `src/strategy/`：框架（Strategy/注册表/K 线/MTF/watchlist）、`signals/`、`algos/`，
  以及资金费率套利、价格监控、回测、交易台账四个功能域。
- `src/observability/`：指标接口和结构化日志。

## 当前验证结果

测试范围和收集数量以 `uv run --locked pytest --collect-only -q` 为准，网络测试需要外部交易所凭据和可用环境，不能作为默认本地验证条件。
执行可靠性的故障注入、迁移和锁用例见[交易执行可靠性](../design/base-execution-reliability.md)。

已完成一次三家测试网只读验证：Arcus `BTC-USD`、Hyperliquid `BTC/USDC:USDC`、Binance
`BTC/USDT:USDT` 的市场和盘口均可读取；本地 Ethereum 钱包已成功用于 Hyperliquid 测试网账户查询。
Arcus 私有接口仍需独立 Ed25519 API 凭据，Binance 私有接口仍需有效的 Demo HMAC API Key/Secret。

默认验证命令：

```bash
uv run --locked pytest -m "not network"
uv run --locked --group docs mkdocs build --strict --site-dir /share/wangziping/tmp/omnitrade-mkdocs-site
```

## 不属于当前实现

未在源码、配置和测试中同时得到确认的未来 HTTP API、具体交易所 native adapter 能力、监控配置和 Agent SDK 产品化集成，不属于当前实现文档。跨交易所价差套利已具备离线协调、Arcus WS 事件恢复、测试网执行门控和周期持久化；主网实盘执行不属于当前实现。测试钱包和 API 凭据保存在仓库外的本地权限文件，不进入源码、配置示例或文档。
