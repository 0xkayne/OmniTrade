---
status: design
authority: normative
owner: project maintainers
updated: 2026-09-14
applies_to: cross-venue price arbitrage and src/arbitrage/
---

# 跨交易所价差套利

本文定义 Arcus 与 Hyperliquid、Binance 之间的真实跨市场套利设计。套利成交必须产生真实的风险转移；交易量不是策略目标，也不能通过自成交或关联账户对敲制造成交量。

## 范围和边界

首期只支持同一基础资产的线性永续合约对冲。每个周期在一个交易所建立多头、另一个交易所建立等 delta 的空头，价差收敛或风险条件触发时同时平仓。现货-永续、反向合约、期权和跨结算币种对冲需要单独的估值和保证金模型，首期拒绝。

交易所适配器只负责协议、签名、订单和行情转换，套利决策放在独立的策略域。Arcus native adapter 不应包含套利规则。

## 净价差模型

信号必须基于目标数量的盘口深度 VWAP，而不是中间价：

```text
gross_edge = sell_vwap - buy_vwap
net_edge = gross_edge
            - maker/taker fees
            - estimated slippage
            - funding cost
            - latency reserve
            - transfer and margin costs
```

只有在 `net_edge_bps >= min_net_edge_bps`、报价未过期、盘口深度足够且两边保证金通过检查时才允许开仓。数量以基础资产 delta 统一，必须考虑合约乘数、最小数量、价格精度和舍入损失。

## 模块边界

建议实现目录如下：

```text
src/arbitrage/
├── config.py          # YAML 映射的类型化配置契约
├── models.py          # ArbPair、QuoteSnapshot、Opportunity、ArbCycle、ArbFill
├── symbol_map.py      # venue symbol、乘数、精度和 hedge ratio
├── market_scanner.py  # L2 盘口、标记价和 funding 数据
├── opportunity.py    # 深度 VWAP、费用和净价差
├── risk.py            # 裸露、保证金、频率和熔断规则
├── executor.py        # 离线安全的双腿开仓、对冲和平仓协调器
├── canary.py          # 确认串保护的单周期测试网开仓后平仓
├── lifecycle.py       # 周期状态、净敞口和 PnL
└── recovery.py        # 重启后的订单查询、敞口重建和恢复决策
```

策略层可以复用 `ExchangeFactory`、`BaseExchange`、`OrderRequest`、`OrderSnapshot` 和持久化设施。第三阶段在保留离线模拟的同时增加 Arcus 原生 WS 成交回报、测试网执行门控和重启恢复；第四阶段增加受保护的单周期 Canary。真实下单仍必须显式选择 `testnet`，主网在执行器边界拒绝。执行器使用 `a:open:0`、`a:hedge:0`、`a:close:0` 这类稳定腿身份，避免恢复或平仓时复用开仓 client ID。

## 周期状态机

```text
DETECTED -> PRECHECKED -> OPENING -> HEDGING -> OPEN
                                     |          |
                                     v          v
                                UNHEDGED    CLOSING -> CLOSED
                                     |
                                     v
                                  RECOVERY -> MANUAL_REVIEW
```

首期推荐 `IOC-IOC`：两边发送受保护的 IOC 限价单，按实际成交量计算 delta，再立即对冲未匹配部分。任何拒单、超时、未知状态或撤单竞态都必须暂停新信号并进入恢复流程。后续才考虑 maker-taker，因为被动腿会增加单腿暴露和逆向选择风险。

## 风控

至少需要以下限制：`max_cycle_notional_usd`、`max_open_cycles`、`max_unhedged_qty`、`max_unhedged_usd`、`max_unhedged_ms`、`min_net_edge_bps`、`max_quote_age_ms`、`max_slippage_bps`、`max_margin_usage_pct`、`daily_loss_limit_usd` 和 `max_consecutive_failures`。

以下事件触发全局 kill switch：Arcus WS 序列断裂、行情过期、一侧 API 连续失败、对冲延迟超限、净敞口超限、保证金不足或日亏损达到上限。恢复前禁止创建新周期。

## 数据和审计

每个 `ArbCycle` 必须保存配对、方向、目标和实际成交数量、两腿订单 ID、预期净价差、实际手续费、资金费、滑点、最大裸露时间、剩余敞口、最终 PnL 和关闭原因。逐笔成交保存交易所时间与本地接收时间，便于复盘延迟和证明真实风险转移。

## 配置示例

```yaml
arbitrage:
  enabled: false
  dry_run: true
  # order policy; both legs must use IOC in stage three
  execution_mode: ioc_ioc
  # hedged executor network mode: offline, testnet, or mainnet
  hedged_execution_mode: offline
  testnet_confirmed: false
  venues: [arcus, hyperliquid, binance]
  pairs:
    - base: BTC
      market_type: perp
      venue_a: arcus
      symbol_a: BTC-USD
      venue_b: hyperliquid
      symbol_b: BTC/USDC:USDC
  min_net_edge_bps: 15
  max_quote_age_ms: 500
  max_unhedged_ms: 1500
  max_unhedged_usd: 100
  max_cycle_notional_usd: 1000
  max_open_cycles: 1
  max_slippage_bps: 8
  cooldown_seconds: 30
```

主网必须单独使用密钥和 endpoint，默认关闭，并先经过 replay、paper、testnet 和 canary 阶段。当前测试网执行器允许 Arcus、Hyperliquid、Binance 三家之间的任意两所线性永续配对；`onefill arb testnet-smoke` 只做三家只读连通性检查，不发送订单。测试网执行器要求两侧均为 `NetworkType.TESTNET`、支持 IOC 和 client order ID，并要求持久化周期后才允许提交。Arcus 的 `202 ACK` 只表示请求受理，必须等 `orders`/`userFills` 或 REST client ID 查询得到最终证据。

## 第四阶段：测试网 Canary

第四阶段通过 `TestnetCanary` 和受保护的 `onefill arb testnet-canary` 入口验证一条完整链路：

1. 仅允许 Arcus、Hyperliquid、Binance 中选择两个交易所，并解析双方当前测试网线性永续合约。
2. 读取账户、能力和盘口，检查 client order ID、IOC、数量和默认 25 USD 最大名义金额。
3. 提交一次双腿开仓，确认两边最终状态后立即提交反向双腿平仓。
4. 任意未知状态停止自动动作并进入 `RECOVERY`，不自动重试；主网、连续运行和放大名义金额不属于本阶段。

入口必须携带精确确认串，示例：

```bash
uv run --locked onefill arb testnet-canary \
  --venue-a arcus --venue-b hyperliquid --base BTC --quantity 0.0001 \
  --max-notional-usd 25 --confirm TESTNET_CANARY --json
```

Arcus 的 Ed25519 API key/secret、Hyperliquid 的 EVM 私钥和 Binance Demo HMAC key/secret
必须分别配置在本地 secrets 文件中。缺少任一方凭据时，Canary 在提交前拒绝；只读 smoke
检查不要求这些私有凭据。

## 验收顺序

先验证盘口回放、深度 VWAP、费用模型、合约映射和故障注入；再验证 Arcus 的 WS 重连、序列恢复和 `userFills`；使用 `testnet-smoke` 完成只读连通性检查后，再使用精确确认串运行一次小额 Canary。主网仍不属于本阶段。验收指标包括实际净 PnL、裸露数量和持续时间、订单恢复率、报价延迟及所有费用。
