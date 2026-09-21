---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: src/arbitrage/
---

# Cross-Venue Arbitrage API

`src.arbitrage` 提供跨交易所价差计算、生命周期和离线模拟执行能力。扫描器和风控只接收
市场层对象；`HedgedExecutor` 仅在显式 `simulate=True` 且所有适配器为 `MockExchange` 时
套利执行默认是离线模拟。第四阶段增加受保护的单周期测试网 Canary，主网执行始终被拒绝。

`HedgedExecutor` 的 `execution_mode` 可取 `offline`、`testnet` 或 `mainnet`：

- `offline` 必须同时传入 `simulate=True`，并且所有适配器都必须是 `MockExchange`。
- `testnet` 必须传入 `testnet_confirmed=True`，两条腿必须来自 Arcus、Hyperliquid、Binance，所有适配器必须报告 `NetworkType.TESTNET`，且两条腿都具备 client order ID 和 IOC 能力。
- `mainnet` 直接返回拒绝结果。

`TestnetCanary` 是 CLI 和 Python API 共用的测试网边界。它要求显式确认字符串
`TESTNET_CANARY`，先读取两边账户和盘口，再以不超过默认 25 USD 的单周期 IOC 限价单开仓，
成功后立即平仓。任何凭据、能力、盘口或成交确认失败都不会自动重试；未知状态交给恢复流程。
Arcus 需要 Ed25519 API 凭据，Hyperliquid 需要 EVM 钱包私钥，Binance 需要测试网 HMAC
API Key/Secret。

Arcus 下单返回的 `202 ACK` 不是成交确认。适配器通过 `orders` 和 `userFills`
频道获取最终状态，并在网络超时后按 client ID 查询，未确认的订单保持
`unknown`，交由恢复流程处理。

Hyperliquid 和 Binance 的 CCXT 适配器没有声明私有 `userFills` 流；执行器会使用
订单查询和 client order ID 轮询确认成交，只有显式声明该能力的适配器才会等待私有成交流。

`ArbitrageRecovery` 读取 `arbitrage_cycles.execution_context_json` 和每条腿的稳定
订单身份，刷新累计成交及逐笔 fills，返回 `OPEN`、`CLOSED`、`RECOVERY` 或
`MANUAL_REVIEW`。恢复过程只查询和持久化，不提交或撤销订单。

## Public API

::: src.arbitrage
    options:
      show_root_heading: true
      members:
        - ArbPair
        - QuoteSnapshot
        - SpreadOpportunity
        - ArbFill
        - ArbCycle
        - ArbitrageConfig
        - ArbitragePairConfig
        - normalize_symbol
        - pair_from_instruments
        - common_base_quantity
        - estimate_vwap
        - evaluate_spread
        - ScannerConfig
        - CrossVenueArbitrageScanner
        - HedgedExecutor
        - CanaryRequest
        - CanaryResult
        - TestnetCanary
        - ArbitrageRecovery
        - ArbitrageRiskConfig
        - ArbitrageRiskValidator
        - RiskDecision
