---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: src/, tests/, config/ and onefill CLI
---

# 当前实现状态

本文档是当前代码状态的简短快照，不是开发计划。功能变更后应重新核对源码和测试，并直接更新本文件；旧快照不保留为规范。

## 已实现入口

`onefill` 当前包含以下顶层命令：

- 核心执行：`order`、`query`、`status`、`binance-smoke`、`list-intents`、`cancel`、`ack`、`recover`、`venues`、`instruments`
- 资金费率套利：`arb scan`、`arb run`、`arb positions`、`arb history`、`arb testnet-smoke`、`arb testnet-canary`
- 价格监控：`watch run`、`watch backfill`
- 交易台账：`trades record`、`trades list`、`trades export`
- 回测：`backtest run`

命令参数和退出码以 [CLI Reference](api/cli.md) 与 `src/cli/main.py` 为准。

## 已实现模块

目录层级与依赖方向见 [代码目录结构规范](../standards/directory-structure.md)。

- `src/cli/`：Typer 命令、`bootstrap` 装配和程序化 Intent 入口；`config.py` 统一按网络加载 `secrets.testnet.yaml` / `secrets.mainnet.yaml`，公共 Telegram 凭据保留在 `secrets.yaml`，网络与凭据不跨环境回退；`arb testnet-smoke` 支持 Arcus、Hyperliquid、Binance 的只读测试网市场/盘口检查和可选账户查询；`arb testnet-canary` 受确认串、空仓无挂单基线、IOC、client ID、固定价格保护和每笔名义金额门控，只有最终仓位为零且无挂单才记为关闭。
- `src/coordinator/`：Intent 规划、校验、风险校验、固定价格保护、顺序拆单、幂等订单确认和失败回滚；显式恢复中断 Intent。
- `src/market/`：Asset、Instrument、NetworkType、Quote、InstrumentRegistry 和行情获取。
- `src/arbitrage/`：Arcus、Hyperliquid、Binance 三家之间的永续合约配对归一化、深度 VWAP、净价差评估、并发机会扫描、机会级风控、周期状态机、离线模拟、测试网门控的 `HedgedExecutor`、单周期 `TestnetCanary` 和重启恢复决策。默认仍为 dry-run；主网执行被拒绝。
- `src/exchange/`：BaseExchange 抽象、Binance 现货/U本位/币本位永续的独立客户端路由、通用 CCXT 适配、Arcus native REST/WS 适配、`ExchangeFactory`、订单簿缓存和测试替身。Arcus 支持频道隔离、盘口序列断档重订阅、WS 重连及 client ID 查询；Arcus/Hyperliquid 均提供 typed 账户/仓位快照及订单/成交 WS 事件。Hyperliquid 统一账户等非单资产模式仍被普通 Coordinator 永续执行拒绝。
- `src/persistence/`：SQLite 状态、JSONL 审计以及策略数据表；只读写行，不构造领域对象。
- `src/strategy/`：框架（Strategy/注册表/K 线/MTF/watchlist）、`signals/`、`algos/`，
  以及资金费率套利、价格监控、回测、交易台账四个功能域。
- `src/observability/`：指标接口和结构化日志。

`binance-smoke` 三产品分开只读检查；显式 `--allow-orders` 与预算才运行持久化开仓/补偿周期。
Binance 订单能力的验证依据是离线测试，未执行真实 Demo 订单。
2026-09-29 已使用空凭据读取 Binance Demo 三产品目录及 `BTC/USDT`、
`BTC/USDT:USDT`、`BTC/USD:BTC` 的公开盘口；该检查不包含私有账户或成交验证。

Binance 执行限定普通账户、单向持仓、单资产保证金；交割与币本位套利不在范围内。开平仓、原生数量和基线恢复见 [Binance 接入](../design/base-binance-integration.md)。

## 验证入口与证据边界

测试范围和收集数量以 `uv run --locked pytest --collect-only -q` 为准，网络测试需要外部交易所凭据和可用环境，不能作为默认本地验证条件。
执行可靠性的故障注入、迁移和锁用例见[交易执行可靠性](../design/base-execution-reliability.md)。

`tests/e2e/test_dex_testnet.py` 提供 Arcus / Hyperliquid 真实测试网套件，默认只读；
显式 `--dex-testnet-trades` 才运行小额交易。目标 25 USD、每笔最高 100 USD、整轮最高
5000 USD，固定中价 ±0.5% 价格保护，并保护已有仓位、订单和现货资产。每轮在 `/share`
下创建独立数据库和报告，分别记录 `PASS`、`FAIL`、`BLOCKED`、`UNSUPPORTED`。
WS 必须有真实关联事件，恢复回读只查询、不重发；低层覆盖不代表普通 Intent 全面支持。
参数、场景和输出路径见[DEX 测试网验证](../../user-guide/examples/dex-testnet-validation.md)。

测试源码和离线验证不是一次真实交易验收的结论。Arcus / Hyperliquid 账户数据公开可读，
只读成功不证明签名权限；真实交易结果必须依据对应运行报告和订单证据判定。

默认验证命令：

```bash
uv run --locked --extra dev --group docs pytest -m "not network"
uv run --locked --extra dev --group docs mkdocs build --strict --site-dir /share/$USER/tmp/omnitrade-mkdocs-site
```

## 不属于当前实现

未在源码、配置和测试中同时得到确认的未来 HTTP API、具体交易所 native adapter 能力、监控配置和 Agent SDK 产品化集成，不属于当前实现文档。跨交易所价差套利已具备离线协调、Arcus WS 事件恢复、测试网执行门控和周期持久化；主网实盘执行不属于当前实现。测试钱包和 API 凭据保存在仓库外的本地权限文件，不进入源码、配置示例或文档。
