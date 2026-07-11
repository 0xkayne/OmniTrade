# Configuration

oneFill uses three YAML configuration files. All live in the `config/` directory.

## `config/exchanges.yaml`

Defines each connected venue. The schema:

```yaml
exchanges:
  binance:
    type: ccxt                    # adapter type (currently only "ccxt")
    enabled: true                 # set false to skip during startup
    default_network: testnet      # or mainnet
    networks:
      mainnet:
        rest_base_url: "https://api.binance.com"
        websocket_url: "wss://stream.binance.com:9443"
      testnet:
        rest_base_url: "https://testnet.binance.vision"
        websocket_url: "wss://testnet.binance.vision"
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
```

### Switching networks

Set `default_network` to `testnet` or `mainnet`. You can override at runtime with `--network testnet` or `--network mainnet` on the `onefill order` command.

For Binance with `default_network: testnet`, oneFill automatically enables ccxt's `enable_demo_trading(True)`.

### Adding a new venue

See the [Exchange Integration Guide](../design-docs/exchange-integration-guide.md) (中文) for step-by-step instructions.

## `config/secrets.yaml`

Credentials file — **gitignored**, never committed. Schema differs per venue:

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

Copy `config/secrets.example.yaml` as a starting point:

```bash
cp config/secrets.example.yaml config/secrets.yaml
```

## `config/risk.yaml`

Pre-trade guardrails. Every intent passes through `RiskValidator` before any orders are sent.

```yaml
max_notional_per_intent: 100000    # USD — reject intents above this
daily_loss_limit_usd: 10000        # USD — reject if cumulative PnL today exceeds this loss
max_venue_exposure_usd: 50000      # USD — reject if any venue has too much outstanding
rate_limit:
  max_orders: 10                    # max intents per sliding window
  window_seconds: 60                # sliding window duration
```

Set any value to `null` to disable that check.

Risk failures appear in `--json` output as `risk_failures` and in the terminal as rejection reasons.
