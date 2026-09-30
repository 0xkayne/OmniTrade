---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
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
`TESTNET_CANARY`，先确认两边同 symbol 零仓位、无挂单，且持久化 store 无未完成或
`MANUAL_REVIEW` 周期。每笔 IOC 限价单默认最多 25 USD、硬上限 100 USD；开平仓均以
最新盘口中价 ±0.5% 为界，检查有效 tick、数量步长、最低金额与区间内深度。
实际限价从中价 ±0.4% 向内取整，为测试套件发送前的实时复核留出 0.1 个百分点余量。
开仓成功后重新报价平仓，启用 `HedgedExecutor(verify_flat_on_close=True)`，最终实际仓位
为零且无挂单后才能保存 `CLOSED`。平仓保护失败、提交未知、异常或任务取消保留恢复状态
及稳定订单身份；取消随后继续向调用方传播。`CanaryResult.cycle_id` 可用于定位持久化证据。
未知请求不重发；已确认的部分成交不平衡仍由执行器使用新的 hedge client ID 做有界对冲。
Arcus 需要 Ed25519 `api_key` / `api_signing_key` 和 `master_wallet_address`；Hyperliquid 使用
`master_wallet_address`、`api_wallet_address`、`api_wallet_private_key`，API Wallet 必须
在同网络已获授权且独立于主钱包；Binance 需要测试网 HMAC API Key/Secret。
账户读取成功不能证明 Arcus/Hyperliquid 签名权限，字段映射见[凭据指南](../../../user-guide/configuration/credentials.md)。

Arcus 下单返回的 `202 ACK` 不是成交确认。适配器通过 `orders` 和 `userFills`
频道获取最终状态，并在网络超时后按 client ID 查询，未确认的订单保持
`unknown`，交由恢复流程处理。

Arcus 与 Hyperliquid 声明 `supports_user_fills`，由 `watch_orders` / `watch_user_fills`
提供归一化事件；REST 订单和 client ID 查询仍参与成交确认。Binance 按其适配器能力
选择确认路径，不假设所有 venue 都有相同账户流。

[DEX 测试网验证](../../../user-guide/examples/dex-testnet-validation.md)把单所生命周期、
双向 Canary、真实 WS 事件和独立数据库回读组合为专用验收入口。默认只读，交易需显式
开启，整轮预算由该套件统一控制；低层验收不等于普通 Coordinator 支持全部账户模式。

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
