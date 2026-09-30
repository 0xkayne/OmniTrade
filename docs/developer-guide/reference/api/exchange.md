---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: src/exchange/
---

# Exchange Package

The only package that talks to a trading venue. `BaseExchange` is the port;
`CCXTExchange` is the CCXT implementation, native adapters implement venue
protocols directly, and `ExchangeFactory` builds either kind from `config/exchanges.yaml`.

Layer design: [Exchange Layer](../../design/base-exchange-layer.md).
Adding a venue: [Exchange Integration](../../design/base-exchange-integration.md).

Arcus and Hyperliquid implement typed account and position snapshots for baseline and exposure
validation. Hyperliquid reports its account abstraction explicitly; ordinary Coordinator perpetual
execution continues to reject unified and other non-single-asset modes. Both adapters expose
normalized account order/fill events. See [DEX testnet validation](../../../user-guide/examples/dex-testnet-validation.md)
for bounded live checks and the distinction between adapter evidence and full Intent support.

## BaseExchange

::: src.exchange.base
    options:
      show_root_heading: true
      heading_level: 2

## CCXTExchange

Hyperliquid account streams reuse one wire subscription per `type` + `user` for `orderUpdates` and
`userFills`. Per-symbol futures, message hashes and caches remain separate, so multiple market consumers
do not steal each other's updates. Server error frames reject consumers with an exception object instead
of passing a string to the WebSocket error path. This behavior does not turn an initial snapshot or a
subscription ACK into evidence of a new order or fill.

::: src.exchange.ccxt.CCXTExchange
    options:
      show_root_heading: true
      heading_level: 2

## BinanceExchange

Binance uses fixed spot/usdm/coinm clients behind one venue identity. Typed account and order
methods route by Instrument; generic operations that cannot select a family reject explicitly.
See [Binance integration](../../design/base-binance-integration.md).

::: src.exchange.binance.BinanceExchange
    options:
      show_root_heading: true
      heading_level: 2

## Native adapters

Native adapters live under `src.exchange` and implement only the verified
capabilities of their venue while preserving the `BaseExchange` contracts.
Arcus is implemented as `src.exchange.arcus.ArcusExchange` and selected with
`type: native` and `adapter: arcus`.

For Arcus permission errors, a successful public account GET is insufficient evidence. Match the
configured public key in `GET /v1/apiKeys`, then inspect `ACTIVE` status, `validUntil`,
`accountIndex` and `allSubaccounts`. The target `options.account_index` in `config/exchanges.yaml`,
funded subaccount and key scope must agree; changing the index does not move funds or grant access.
See [credential troubleshooting](../../../user-guide/configuration/credentials.md#arcus-api-key-scope).

Adapters that expose private execution streams may implement the optional
`BaseExchange.watch_user_fills()` hook. Recovery can also use
`BaseExchange.fetch_order_by_client_id()` when a submission has a client order
identifier but no server order identifier; adapters should return `None` when
the venue cannot resolve the identifier without submitting another order.

::: src.exchange.arcus.ArcusExchange
    options:
      show_root_heading: true
      heading_level: 2

## ExchangeFactory

::: src.exchange.factory
    options:
      show_root_heading: true
      heading_level: 2

## MockExchange

The test double. Production code must not import it.

::: src.exchange.mock
    options:
      show_root_heading: true
      heading_level: 2
