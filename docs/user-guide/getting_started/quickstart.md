---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: onefill CLI users
---

# Quick Start

Get oneFill running in under 5 minutes.

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
cp config/secrets.example.yaml config/secrets.yaml
```

Edit `config/secrets.yaml` with your credentials:

三家交易所的获取入口、测试网区别、权限建议和验证命令见[交易所凭据指南](../configuration/credentials.md)。Binance 四类产品的 endpoint 矩阵见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。

=== "Binance (demo trading)"

    ```yaml
    binance:
      apiKey: "your-binance-api-key"
      secret: "your-binance-secret"
    ```

    !!! tip
        先按[交易所凭据指南](../configuration/credentials.md)从 Binance 账户内进入 Demo Trading 并创建 HMAC key。
        `demo.binance.com` 可能因地区或账户资格无法直接访问；oneFill 会在 `default_network: testnet`
        时通过 ccxt 自动选择对应的 Demo API endpoint。

=== "Hyperliquid (testnet)"

    ```yaml
    hyperliquid:
      walletAddress: "0x..."
      privateKey: "0x..."
    ```

=== "Arcus (testnet)"

    ```yaml
    arcus:
      api_key: "your_ed25519_public_key"
      private_key: "your_ed25519_private_key"
      address: "0x..."
    ```

    !!! note
        Arcus 使用 Robinhood Chain Testnet（Chain ID `46630`）。API key 注册需要 EVM 钱包的 EIP-712 签名；新账户需要在 Arcus 测试网应用中执行 Testnet Deposit 后才有交易抵押金。

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
