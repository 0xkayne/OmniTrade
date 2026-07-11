# User Guide

Welcome to the oneFill user guide. This section covers everything you need to get started with oneFill, from installation to advanced usage.

## What you'll find here

- **[Quick Start](quickstart.md)** — Install dependencies, configure credentials, and run your first coordinated order
- **[CLI Reference](cli-reference.md)** — Complete reference for all 8 `onefill` commands and 4 `arb` subcommands
- **[Configuration](configuration.md)** — Detailed breakdown of `exchanges.yaml`, `secrets.yaml`, and `risk.yaml`
- **[Risk Controls](risk-controls.md)** — Pre-trade guardrails: notional limits, daily loss, venue exposure, rate limiting

## Key concepts

Before diving in, it helps to understand a few core concepts:

- **Intent** — a user's trading goal: "buy $1000 of BTC, split 50/50 across Binance and Hyperliquid"
- **Leg** — one venue's portion of the intent. An intent with two venues has two legs
- **Plan** — the output of instrument selection + quote fetching + fill estimation. Shows what will happen before any orders go out
- **Coordinated final state** — every leg fills, or partial fills get auto-compensated (reverse orders), or the system blocks until a human intervenes

oneFill is an **execution tool, not a strategy tool**. It doesn't decide whether to trade — it executes your already-decided intent across multiple venues simultaneously.
