---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/legacy/
---

# Legacy Package

The pre-oneFill volume-farming / arbitrage bot. Documented for compatibility only — new functionality must use `coordinator`, `market`, `strategy`, `persistence` or `exchange`.

See [Legacy Mode](../../design/legacy-bot.md) for how it coexists with oneFill and how to run it.

!!! warning "Legacy code"
    These engines use Chinese docstrings and predate the oneFill architecture. They are preserved for backward compatibility and are on the phase-out path.

## VolumeEngine

::: src.legacy.volume_engine
    options:
      show_root_heading: true
      heading_level: 2

## ArbitrageEngine

::: src.legacy.arbitrage_engine
    options:
      show_root_heading: true
      heading_level: 2
