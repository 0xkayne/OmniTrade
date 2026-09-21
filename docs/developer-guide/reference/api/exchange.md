---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/exchange/
---

# Exchange Package

The only package that talks to a trading venue. `BaseExchange` is the port;
`CCXTExchange` is the CCXT implementation, native adapters implement venue
protocols directly, and `ExchangeFactory` builds either kind from `config/exchanges.yaml`.

Layer design: [Exchange Layer](../../design/base-exchange-layer.md).
Adding a venue: [Exchange Integration](../../design/base-exchange-integration.md).

## BaseExchange

::: src.exchange.base
    options:
      show_root_heading: true
      heading_level: 2

## CCXTExchange

::: src.exchange.ccxt.CCXTExchange
    options:
      show_root_heading: true
      heading_level: 2

## Native adapters

Native adapters live under `src.exchange` and implement only the verified
capabilities of their venue while preserving the `BaseExchange` contracts.
Arcus is implemented as `src.exchange.arcus.ArcusExchange` and selected with
`type: native` and `adapter: arcus`.

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
