---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-30
applies_to: config/exchanges.yaml, config/secrets*.yaml, config/risk.yaml, config/watchlist.yaml, config/arbitrage.yaml
---

# Configuration

Omnitrade 的用户运行配置位于 `config/` 目录。交易所定义和风险配置由 `exchanges.yaml`、`risk.yaml` 共用；
交易所凭据按网络存放在 `secrets.testnet.yaml` 和 `secrets.mainnet.yaml`，`secrets.yaml` 只保存
Telegram 等公共凭据。价格监控还会读取 `watchlist.yaml`。
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
    market_families: [spot, usdm] # add coinm explicitly for inverse perpetuals
    networks:
      mainnet: {}
      testnet: {}                # fixed Demo endpoints derived by the adapter
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

网络选择同时决定地址和凭据文件：显式 `--network` / Python `target_network` 优先，
未指定时使用各交易所的 `default_network`，该字段也未配置时使用 `testnet`。各交易所
可以采用不同默认网络；此时分别从对应网络文件读取该交易所的凭据。
`arb testnet-smoke` 和 `arb testnet-canary` 固定使用测试网及测试网凭据。

Binance 的产品、Legacy Testnet、Demo Trading endpoint 和 CCXT 映射见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。
`default_network: testnet` 对 Binance 固定启用 ccxt 的 `enable_demo_trading(True)`，
现货和合约都使用 Demo Trading。默认凭据在
[Demo API Management](https://demo.binance.com/zh-CN/my/settings/api-management) 创建。
独立的 [Spot Testnet](https://testnet.binance.vision/) 仍然存在，但本项目不接入它或旧
Futures Testnet；二者仅在 Binance 参考文档中说明，不能将其 key 填入默认测试网配置。
Binance 使用三个固定产品客户端；URL 由锁定的 CCXT 版本按网络和产品派生，配置不复制地址。
默认 `market_families: [spot, usdm]`；使用币本位永续时显式增加 `coinm`。
网络切换需要重建客户端并重新加载对应凭据，不能原地切换或回落到另一网络。
各家族独立检查账户权限，公开行情成功不表示私有接口或下单权限可用。

### Adding a new venue

See the [Exchange Integration Guide](../../developer-guide/design/base-exchange-integration.md) for step-by-step instructions.

## 交易所凭据：`secrets.testnet.yaml` / `secrets.mainnet.yaml`

两个文件均被 Git 忽略。每个文件必须有与所选网络一致的顶层 `network` 标记；
交易所内部字段保持相同格式。各交易所凭据的获取入口、网络区别和验证步骤见
[交易所凭据指南](credentials.md)。

```bash
cp config/secrets.testnet.example.yaml config/secrets.testnet.yaml
cp config/secrets.mainnet.example.yaml config/secrets.mainnet.yaml
chmod 600 config/secrets.testnet.yaml config/secrets.mainnet.yaml
```

所有交易所凭据值使用双引号字符串，未填写用 `""`，不用裸值或 `null`，以免 YAML 把
`0x` 或纯数字解析为数值并丢失格式。单引号若已解析为字符串，不因样式被运行时拒绝。
此规则不改变 `exchanges.yaml` 中布尔、数值等业务配置的类型。

测试网文件示例：

```yaml
network: "testnet"
binance:
  apiKey: "your_binance_demo_api_key"
  secret: "your_binance_demo_secret"
hyperliquid:
  master_wallet_address: "<funded-master-account-address>"
  api_wallet_address: "<approved-api-wallet-address>"
  api_wallet_private_key: "<approved-api-wallet-private-key>"
  # vaultAddress: "0x..."
arcus:
  api_key: "your_arcus_ed25519_public_key"
  api_signing_key: "your_arcus_api_signing_key"
  master_wallet_address: "0x0000000000000000000000000000000000000000"
```

主网文件使用 `network: "mainnet"`，分别填写主网凭据。切换网络后自动选择对应文件，
不再需要替换同一组 key。Binance 使用 HMAC（`apiKey` + `secret`），测试网文件填写
Demo Trading key。Hyperliquid 的 `master_wallet_address` 是有资金的主账户地址；
`api_wallet_address` 和 `api_wallet_private_key` 是同网络已授权 API Wallet 的地址与私钥。
两个 API 字段须同时填写，推导地址须匹配且不同于主钱包；同时留空则仅支持公开只读查询。
旧 `walletAddress` / `wallet_address` / `privateKey` / `private_key` 字段必须迁移并移除，
不能将主钱包私钥直接改名为 API Wallet 私钥。
Arcus 网页的 API Key / API Signing Key 分别填入 `api_key` /
`api_signing_key`（均为 32 字节 Ed25519、64 位十六进制，仅 `api_signing_key` 允许 `0x` 前缀），
`master_wallet_address` 是授权主钱包地址。
Arcus 与 Hyperliquid 都是主账户授权独立 API 密钥，但 Arcus `api_key` 是 Ed25519 原始公钥，
Hyperliquid `api_wallet_address` 是 secp256k1 公钥派生的 EVM 地址，两者和各自私钥均不可互换。
不能把 Ethereum 私钥填入 Arcus 的 `api_signing_key`。旧 Arcus `address` / `wallet_address`
改为 `master_wallet_address`；旧 `apiKey`、`private_key`、`privateKey` 也必须重命名并移除，
不提供兼容回退。Arcus 协议字段 `address` / `ad` 不变。具体字段对照见[凭据指南](credentials.md)。

只读取实际需要的网络文件；文件存在但网络标记或 YAML 结构错误时，在连接前报错。
公开行情允许缺少凭据，私有操作仍需要对应交易所的有效认证信息。不会从另一网络或
旧 `secrets.yaml` 回退读取交易所凭据，也无法从 key 字符串本身判断其签发环境。

Python 调用方可设置 `secrets_config_path` 指向一个自定义网络文件；其 `network` 必须
匹配所有选中交易所的最终网络。需要混合网络时使用默认自动选择方式。
默认网络文件位于 `exchanges_config_path` 所在目录，详见 [Python API](../api/index.md)。

## 公共凭据：`config/secrets.yaml`

该文件只保存 Telegram 等与交易网络无关的凭据，同样被 Git 忽略：

```bash
cp config/secrets.example.yaml config/secrets.yaml
chmod 600 config/secrets.yaml
```

```yaml
telegram:
  bot_token: "your_telegram_bot_token"
  chat_id: "your_telegram_chat_id"
```

监控构建函数通过独立的 `common_secrets_config_path` 读取公共凭据，默认路径是
`exchanges_config_path` 同目录下的 `secrets.yaml`。仅运行交易所功能时无需配置 Telegram。

### 从旧单文件迁移

把旧 `secrets.yaml` 中的交易所条目移到其凭据实际所属网络的文件，并添加顶层
`network` 标记；公共文件保留 `telegram` 等公共条目。迁移前确认凭据属于哪个网络，
不要复制同一组 API key 到两套环境。验证新文件内容和权限后再删除旧文件中的交易所条目。
已有目标文件时检查冲突，不覆盖已有凭据。旧无网络标记文件不再用作交易所凭据来源。

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
