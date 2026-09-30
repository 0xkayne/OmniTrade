---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-30
applies_to: onefill CLI users
---

# Quick Start

Get Omnitrade running in under 5 minutes.

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) package manager
- Testnet credentials for at least one supported venue (Binance demo, Hyperliquid testnet, or Arcus testnet)

## 1. Install

```bash
git clone https://github.com/0xkayne/OmniTrade.git
cd OmniTrade
uv sync --group dev
```

## 2. Configure credentials

```bash
cp config/secrets.testnet.example.yaml config/secrets.testnet.yaml
chmod 600 config/secrets.testnet.yaml
```

Edit `config/secrets.testnet.yaml` with your testnet credentials and keep the top-level
`network: "testnet"` marker. Each tab below shows the same marker plus one venue section. For mainnet, use
`config/secrets.mainnet.example.yaml` to create `config/secrets.mainnet.yaml` with
`network: "mainnet"`; switching networks automatically selects the corresponding credentials.
See [Configuration](../configuration/index.md) for existing-file migration and shared Telegram credentials.

三家交易所的获取入口、测试网区别、权限建议和验证命令见[交易所凭据指南](../configuration/credentials.md)。Binance 各产品和环境的 endpoint 矩阵见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。

凭据值一律用双引号包裹，未填写使用 `""`；不要写裸 `0x`、数字或 `null`，以免 YAML 改变类型。
Arcus 的旧 `address` / `wallet_address` 改名为 `master_wallet_address` 并移除旧字段。
完整的统一模型与 UI 对照见[凭据指南](../configuration/credentials.md)。

=== "Binance (demo trading)"

    ```yaml
    network: "testnet"
    binance:
      apiKey: "your_binance_demo_api_key"
      secret: "your_binance_demo_secret"
    ```

    !!! tip
        在 [Demo API Management](https://demo.binance.com/zh-CN/my/settings/api-management)
        创建 HMAC key，并按需开启现货或合约权限。`default_network: testnet` 固定选择 Demo Trading；
        独立的 Spot Testnet 仅在[凭据指南](../configuration/credentials.md)中说明，本项目不接入。

=== "Hyperliquid (testnet)"

    ```yaml
    network: "testnet"
    hyperliquid:
      master_wallet_address: "<funded-master-account-address>"
      api_wallet_address: "<approved-testnet-api-wallet-address>"
      api_wallet_private_key: "<approved-testnet-api-wallet-private-key>"
    ```

    `master_wallet_address` 填有资金的主账户地址，其余两个字段填同一测试网批准的
    API Wallet 地址与私钥。私钥须匹配 API 地址，API 地址须不同于主钱包。
    尚未批准 API Wallet 时将两个 API 字段同时留空，只做公开查询；不填写主钱包私钥。

=== "Arcus (testnet)"

    ```yaml
    network: "testnet"
    arcus:
      api_key: "your_ed25519_public_key"
      api_signing_key: "your_api_signing_key"
      master_wallet_address: "0x..."
    ```

    !!! note
        与 Hyperliquid 一样，主账户授权独立 API 密钥；Arcus 的 API Key 是 Ed25519 原始公钥，不是 EVM API 钱包地址。网页 API Key / API Signing Key 对应 `api_key` / `api_signing_key`；均为 32 字节十六进制值，`master_wallet_address` 是授权主钱包地址。Arcus 使用 Robinhood Chain Testnet（Chain ID `46630`）。API key 注册需要 EVM 钱包的 EIP-712 签名；新账户需要在 Arcus 测试网应用中执行 Testnet Deposit 后才有交易抵押金。

## 3. (Optional) Review risk guardrails

Edit `config/risk.yaml` to adjust:

```yaml
risk:
  max_notional_per_intent: 100000
  daily_loss_limit_usd: 10000
  max_venue_exposure_usd: 50000
  rate_limit:
    max_orders: 10
    window_seconds: 60
```

Set any value to `null` to disable that check.

## 4. Preview your first order

```bash
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 100 \
  --split binance=0.5,hyperliquid=0.5 \
  --network testnet
```

`--dry-run` runs the Planner + Validator + RiskValidator but does not send any orders. Use it to verify instrument selection and quote estimates before committing real funds.

## 5. Execute

```bash
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage-pct 0.3 \
  --network testnet \
  --yes
```

The `--yes` flag skips the interactive confirmation prompt.

## 6. Check the result

```bash
uv run onefill query <intent-id>
```

## 7. Per-leg overrides

Each leg can override `side`, `product`, and `leverage` independently:

```bash
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 500 \
  --split "binance=0.5:buy:spot,hyperliquid=0.5:sell:perp:3"
```

This buys spot on Binance while shorting perp on Hyperliquid with 3× leverage — all in one command.

## Next steps

- [CLI Reference](../cli/index.md) — all commands and flags
- [Configuration](../configuration/index.md) — config file reference
- [Risk Controls](../configuration/risk-controls.md) — guardrail details
- [Examples](../examples/index.md) — runnable examples per feature
