---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-06
applies_to: src/core/
---

# Core Package

Shared exchange abstraction and legacy engines.

## Shared (new + legacy)

### BaseExchange

::: src.core.base_exchange
    options:
      show_root_heading: true
      heading_level: 2

### ExchangeFactory

::: src.core.exchange_factory
    options:
      show_root_heading: true
      heading_level: 2

## Legacy Engines

The following modules are part of the legacy bot and are documented for compatibility only. New functionality must use the dedicated `coordinator`, `market`, `strategy`, `persistence`, or `exchanges` packages.

!!! warning "Legacy Code"
    These engines use Chinese docstrings and predate the oneFill architecture. They are preserved for backward compatibility.

### VolumeEngine

::: src.core.volume_engine
    options:
      show_root_heading: true
      heading_level: 2

### ArbitrageEngine

::: src.core.arbitrage_engine
    options:
      show_root_heading: true
      heading_level: 2
