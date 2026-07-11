# Quick Start

Get oneFill running in under 5 minutes.

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) package manager
- Testnet credentials for at least one supported venue (Binance demo or Hyperliquid testnet)

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

=== "Binance (demo trading)"

    ```yaml
    binance:
      apiKey: "your-binance-api-key"
      secret: "your-binance-secret"
    ```

    !!! tip
        Binance demo trading uses `enable_demo_trading(True)` in ccxt. oneFill handles this automatically when `default_network: testnet` is set.

=== "Hyperliquid (testnet)"

    ```yaml
    hyperliquid:
      walletAddress: "0x..."
      privateKey: "0x..."
    ```

## 3. (Optional) Review risk guardrails

Edit `config/risk.yaml` to adjust:

```yaml
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

- [CLI Reference](cli-reference.md) — all commands and flags
- [Configuration](configuration.md) — config file reference
- [Risk Controls](risk-controls.md) — guardrail details
