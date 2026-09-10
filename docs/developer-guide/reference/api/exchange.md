---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/exchange/
---

# Exchange Package

The only package that talks to a trading venue. `BaseExchange` is the port,
`CCXTExchange` implements it on top of `ccxt.async_support`, and
`ExchangeFactory` builds adapters from `config/exchanges.yaml`.

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
