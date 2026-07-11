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

The following modules are part of the legacy bot and will be phased out once oneFill reaches feature parity.

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
