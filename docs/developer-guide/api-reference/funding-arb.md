---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/strategy/funding_arb/
---

# Funding Arbitrage Strategy

Cross-venue funding rate arbitrage — premium mean-reversion model, spread scanner, and hedged
position lifecycle management. The model and its rationale are in
[Funding Arbitrage](../funding-arbitrage.md); this page indexes the implementation.

## FundingRateComparator

::: src.strategy.funding_arb.comparator
    options:
      show_root_heading: false

## FundingRateMonitor

::: src.strategy.funding_arb.monitor
    options:
      show_root_heading: false

## PremiumTracker

::: src.strategy.funding_arb.premium_tracker
    options:
      show_root_heading: false

## HedgedPositionManager

::: src.strategy.funding_arb.position_manager
    options:
      show_root_heading: false

## AutoArbRunner

::: src.strategy.funding_arb.runner
    options:
      show_root_heading: false
