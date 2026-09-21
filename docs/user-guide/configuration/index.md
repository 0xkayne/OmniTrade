---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: config/exchanges.yaml, config/secrets.yaml, config/risk.yaml, config/watchlist.yaml, config/arbitrage.yaml
---

# Configuration

oneFill 的用户运行配置位于 `config/` 目录。核心执行需要 `exchanges.yaml`、`secrets.yaml` 和 `risk.yaml`；价格监控还会读取 `watchlist.yaml`。
跨交易所价差扫描使用单独的 `config/arbitrage.yaml`，默认关闭且只提供行情扫描与机会预检查。

## `config/exchanges.yaml`

Defines each connected venue. The schema:

```yaml
exchanges:
  binance:
    type: ccxt                    # adapter type: ccxt or native
    # Native venues also set an explicit adapter name, e.g. adapter: arcus.
    enabled: true                 # set false to skip during startup
    default_network: testnet      # or mainnet
    networks:
      mainnet:
        rest_base_url: "https://api.binance.com"
        websocket_url: "wss://stream.binance.com:9443"
      testnet:
        # CCXT enables Demo Trading and selects demo-api/demo-fapi/demo-dapi as needed.
        rest_base_url: "https://api.binance.com"
        websocket_url: "wss://stream.binance.com:9443/ws"
    symbols:
      - BTC/USDT
      - ETH/USDT
    fees:
      taker: 0.001                # 10 bps
      maker: 0.0005               # 5 bps

  hyperliquid:
    type: ccxt
    enabled: true
    default_network: testnet
    networks:
      mainnet:
        rest_base_url: "https://api.hyperliquid.xyz"
        websocket_url: "wss://api.hyperliquid.xyz/ws"
      testnet:
        rest_base_url: "https://api.hyperliquid-testnet.xyz"
        websocket_url: "wss://api.hyperliquid-testnet.xyz/ws"
    symbols:
      - BTC/USDC:USDC
    options:
      hyperliquid:
        filterHip3Markets: false
    fees:
      taker: 0.00025
      maker: 0.0001

  arcus:
    type: native
    adapter: arcus
    enabled: false
    default_network: testnet
    networks:
      mainnet:
        rest_base_url: "https://api.arcus.xyz"
        websocket_url: "wss://api.arcus.xyz/v1/ws"
      testnet:
        rest_base_url: "https://api.testnet.arcus.xyz"
        websocket_url: "wss://api.testnet.arcus.xyz/v1/ws"
    symbols: []                 # Arcus currently exposes perpetuals quoted in USD
    fees:
      taker: 0.0
      maker: 0.0
```

### Switching networks

Set `default_network` to `testnet` or `mainnet`. You can override at runtime with `--network testnet` or `--network mainnet` on the `onefill order` command.

Binance 的产品、Legacy Testnet、Demo Trading endpoint 和 CCXT 映射见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。
当前 `default_network: testnet` 对 Binance 会自动启用 ccxt 的 `enable_demo_trading(True)`，即选择新的 Demo REST profile；它不能选择 Spot Legacy Testnet 或 Futures Legacy Testnet。

### Adding a new venue

See the [Exchange Integration Guide](../../developer-guide/design/base-exchange-integration.md) for step-by-step instructions.

## `config/secrets.yaml`

Credentials file — **gitignored**, never committed. Schema differs per venue:

各交易所凭据的获取入口、网络区别和验证步骤见[交易所凭据指南](credentials.md)。

=== "Binance"

    ```yaml
    binance:
      apiKey: "your-hmac-api-key"
      secret: "your-hmac-secret"
    ```

    !!! note
        Binance uses HMAC authentication (`apiKey` + `secret`). Ed25519 keys are not supported by ccxt.

=== "Hyperliquid"

    ```yaml
    hyperliquid:
      walletAddress: "0x..."
      privateKey: "0x..."
    ```

    !!! note
        Hyperliquid uses wallet-based authentication (Ethereum-style hex). Optional `vaultAddress` for sub-account trading.

=== "Arcus"

    ```yaml
    arcus:
      api_key: "your_arcus_ed25519_public_key"
      private_key: "your_arcus_ed25519_private_key"
      address: "0x0000000000000000000000000000000000000000"
    ```

    !!! note
        Arcus uses an Ed25519 public key as `api_key` and an Ethereum master address. Signed requests use Ed25519; keep the private key only in `secrets.yaml`.

Copy `config/secrets.example.yaml` as a starting point:

```bash
cp config/secrets.example.yaml config/secrets.yaml
```

## `config/risk.yaml`

Pre-trade guardrails. Every intent passes through `RiskValidator` before any orders are sent.

```yaml
risk:
  max_notional_per_intent: 100000    # USD — reject intents above this
  daily_loss_limit_usd: 10000        # USD — reject if cumulative PnL today exceeds this loss
  max_venue_exposure_usd: 50000      # USD — reject if any venue has too much outstanding
  rate_limit:
    max_orders: 10                    # max intents per sliding window
    window_seconds: 60                # sliding window duration
```

Set any value to `null` to disable that check.

Risk failures appear in `--json` output as `risk_failures` and in the terminal as rejection reasons.

## `config/arbitrage.yaml`

该文件定义 Arcus、Hyperliquid 和 Binance 之间的永续合约价差扫描参数。`enabled: false` 和
`dry_run: true` 是默认值；当前代码不会根据此文件直接发送双腿订单。只有在完成成交回报、
对冲恢复、周期持久化和独立实盘验收后，才允许新增经过评审的执行协调器。

```yaml
arbitrage:
  enabled: false
  dry_run: true
  execution_mode: ioc_ioc
  hedged_execution_mode: offline
  testnet_confirmed: false
  venues: [arcus, hyperliquid, binance]
  min_net_edge_bps: 15
  max_quote_age_ms: 500
  max_unhedged_ms: 1500
  max_cycle_notional_usd: 1000
  max_open_cycles: 1
```

完整的状态机、成本模型和上线验收顺序见[跨交易所价差套利设计](../../developer-guide/design/strat-cross-venue-arb.md)。
